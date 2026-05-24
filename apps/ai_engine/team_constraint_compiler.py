"""
TeamConstraint → CP-SAT compiler.

Translates the Notion-filter-style `TeamConstraint` rows (scope × condition ×
quantifier) into CP-SAT clauses against an `assignments[e][d][s]` BoolVar
grid. Pure logic, no Django ORM here — the caller is responsible for
serialising TeamConstraint rows into the dict shape `compile_constraint()`
expects.

Consent invariant
-----------------
Sensitive attributes (gender / height / weight / age) are read from
`employee['attributes']`. The view layer obtains these via
`Employee.sensitive_attributes_for_solver()`, which already returns `None`
for any employee without an active `EmployeeDataConsent`. Hence a condition
that targets a sensitive attribute is automatically *false* for every
unconsented employee — this is the single enforcement point promised in
project_data_consent_ux.

Time-of-day buckets follow shift `start_time`:
  - night      : start_time ≥ 22:00 OR start_time < 05:00
  - morning    : 05:00 ≤ start_time < 12:00
  - afternoon  : 12:00 ≤ start_time < 17:00
  - evening    : 17:00 ≤ start_time < 22:00
"""
from datetime import date as _date, datetime, time
from typing import Any, Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_team_constraints(
    model,
    assignments: Dict[int, Dict[int, Dict[int, Any]]],
    employees: List[Dict[str, Any]],
    shifts: List[Dict[str, Any]],
    days: List[_date],
    team_constraints: Iterable[Dict[str, Any]],
    branch_id: Optional[int] = None,
) -> List:
    """
    Apply each TeamConstraint to the CP-SAT model. Returns a list of soft
    objective terms (already weighted). Hard constraints go straight into the
    model; soft constraints emit a slack variable + penalty term.
    """
    objective_terms: List = []
    for tc in team_constraints:
        if not tc.get('is_active', True):
            continue
        objective_terms.extend(
            _compile_one(model, assignments, employees, shifts, days, tc, branch_id)
        )
    return objective_terms


def employee_matches_condition(
    employee: Dict[str, Any],
    condition_type: str,
    operator: str,
    value: Any,
) -> bool:
    """
    True iff the employee satisfies the condition. Public so tests can pin
    individual matcher behaviour without spinning up a model.
    """
    attrs = employee.get('attributes', {}) or {}
    if condition_type == 'gender':
        actual = attrs.get('gender')
        return _scalar_match(actual, operator, value)
    if condition_type == 'height_cm':
        return _numeric_match(attrs.get('height_cm'), operator, value)
    if condition_type == 'weight_kg':
        return _numeric_match(attrs.get('weight_kg'), operator, value)
    if condition_type == 'age_years':
        return _numeric_match(attrs.get('age_years'), operator, value)
    if condition_type == 'tag':
        return _set_match(set(attrs.get('tag_codes') or []), operator, value)
    if condition_type == 'certification':
        return _set_match(
            set(attrs.get('certification_ids') or []), operator, value
        )
    return False


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# Penalty weight for a soft team constraint per missing unit. Picked to be
# higher than individual preference penalties (3) but lower than the
# fairness/overcap weights so legal hard constraints stay dominant.
SOFT_PENALTY_PER_UNIT = 15


def _compile_one(
    model,
    assignments: Dict[int, Dict[int, Dict[int, Any]]],
    employees: List[Dict[str, Any]],
    shifts: List[Dict[str, Any]],
    days: List[_date],
    tc: Dict[str, Any],
    branch_id: Optional[int],
) -> List:
    """Compile a single constraint; emits hard clauses or soft penalty terms."""
    if tc.get('branch_id') is not None and branch_id is not None:
        if tc['branch_id'] != branch_id:
            return []

    quantifier = tc.get('quantifier', 'at_least')
    quantity = int(tc.get('quantity', 1))
    severity = tc.get('severity', 'hard')

    in_scope_shifts = _shifts_in_scope(shifts, tc)
    if not in_scope_shifts:
        return []
    in_scope_shift_ids = {s['id'] for s in in_scope_shifts}

    eligible_emp_ids = [
        emp['id']
        for emp in employees
        if employee_matches_condition(
            emp,
            tc.get('condition_type'),
            tc.get('condition_operator'),
            tc.get('condition_value'),
        )
    ]

    objective_terms: List = []
    rule_label = tc.get('description') or f"tc#{tc.get('id', '?')}"

    # The constraint applies independently for every (day, shift) cell in
    # scope: "at least N nurses-with-cert-X per night shift, every night".
    for day_idx in range(len(days)):
        for shift in in_scope_shifts:
            shift_id = shift['id']
            if shift_id not in in_scope_shift_ids:
                continue
            cell_sum = sum(
                assignments[emp_id][day_idx][shift_id]
                for emp_id in eligible_emp_ids
            ) if eligible_emp_ids else 0

            if quantifier == 'at_least':
                if severity == 'hard':
                    if eligible_emp_ids:
                        model.Add(cell_sum >= quantity)
                    else:
                        # No eligible employees → cannot meet a positive
                        # at_least requirement. Force infeasibility so the
                        # caller (UI) surfaces the diagnostic rather than
                        # silently delivering a non-compliant schedule.
                        if quantity > 0:
                            model.Add(0 >= quantity)  # always false
                else:
                    slack = model.NewIntVar(
                        0, quantity,
                        f'tc_slack_{rule_label[:12]}_d{day_idx}_s{shift_id}',
                    )
                    model.Add(slack >= quantity - cell_sum)
                    objective_terms.append(SOFT_PENALTY_PER_UNIT * slack)

            elif quantifier == 'at_most':
                if severity == 'hard':
                    model.Add(cell_sum <= quantity)
                else:
                    excess = model.NewIntVar(
                        0, len(eligible_emp_ids) or 1,
                        f'tc_excess_{rule_label[:12]}_d{day_idx}_s{shift_id}',
                    )
                    model.Add(excess >= cell_sum - quantity)
                    objective_terms.append(SOFT_PENALTY_PER_UNIT * excess)

            elif quantifier == 'exactly':
                if severity == 'hard':
                    if eligible_emp_ids:
                        model.Add(cell_sum == quantity)
                    elif quantity > 0:
                        model.Add(0 >= quantity)
                else:
                    # Two-sided slack.
                    over = model.NewIntVar(
                        0, len(eligible_emp_ids) or 1,
                        f'tc_over_{rule_label[:12]}_d{day_idx}_s{shift_id}',
                    )
                    under = model.NewIntVar(
                        0, quantity,
                        f'tc_under_{rule_label[:12]}_d{day_idx}_s{shift_id}',
                    )
                    model.Add(over >= cell_sum - quantity)
                    model.Add(under >= quantity - cell_sum)
                    objective_terms.append(SOFT_PENALTY_PER_UNIT * (over + under))

    return objective_terms


def _shifts_in_scope(
    shifts: List[Dict[str, Any]], tc: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Filter shifts by tc.shift_template_id and tc.scope_time_of_day."""
    selected = shifts
    target_shift_id = tc.get('shift_template_id')
    if target_shift_id is not None:
        selected = [s for s in selected if s['id'] == target_shift_id]
    scope_time = tc.get('scope_time_of_day', 'any')
    if scope_time != 'any':
        selected = [
            s for s in selected
            if _bucket_for_start_time(s.get('start_time', '00:00')) == scope_time
        ]
    return selected


def _bucket_for_start_time(start_str: str) -> str:
    """Map an HH:MM (or HH:MM:SS) start_time string to a time-of-day bucket."""
    try:
        t = datetime.strptime(start_str[:5], '%H:%M').time()
    except (ValueError, TypeError):
        return 'any'
    if t >= time(22, 0) or t < time(5, 0):
        return 'night'
    if t < time(12, 0):
        return 'morning'
    if t < time(17, 0):
        return 'afternoon'
    return 'evening'


# ---- attribute matchers --------------------------------------------------

def _scalar_match(actual: Any, operator: str, value: Any) -> bool:
    if actual is None:
        return False  # consent invariant
    if operator == 'eq':
        return actual == value
    if operator == 'ne':
        return actual != value
    if operator == 'in':
        return actual in (value or [])
    return False


def _numeric_match(actual: Any, operator: str, value: Any) -> bool:
    if actual is None:
        return False  # consent invariant
    try:
        a = float(actual)
        v = float(value) if not isinstance(value, list) else value
    except (TypeError, ValueError):
        return False
    if operator == 'eq':
        return a == v
    if operator == 'ne':
        return a != v
    if operator == 'gte':
        return a >= v
    if operator == 'lte':
        return a <= v
    if operator == 'in' and isinstance(v, list):
        return a in (float(x) for x in v)
    return False


def _set_match(actual: set, operator: str, value: Any) -> bool:
    # `value` is the JSON list of required entries (tag codes or cert ids)
    # for `contains`/`in`, or a single entry for `eq`.
    if operator == 'eq':
        return value in actual
    if operator == 'ne':
        return value not in actual
    if operator == 'contains':
        return set(value or []).issubset(actual)
    if operator == 'in':
        return any(v in actual for v in (value or []))
    return False
