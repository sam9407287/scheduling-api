"""
AI Engine serializers
"""
from rest_framework import serializers
from datetime import date
from .providers.base import ScheduleRequest


class ScheduleRequestSerializer(serializers.Serializer):
    """
    統一排班請求序列化器。

    對應前端三個按鈕：
      * 「全自動生成 B」 — 不傳 seed_version_id；可選 enforce_labor_law
      * 「AI 補齊」     — seed_version_id 指向已部分填好的 B，minimize_drift_from_seed=true
      * 「派生 A」      — seed_version_id 指向 B，minimize_drift_from_seed=true，
                          並隱含 enforce_labor_law=true（後端自動覆蓋）

    Token 消費由 `consume_token` 旗標控制 — 第一階段僅寫入 metadata，
    第二階段才接上真正的扣款交易。
    """
    organization_id = serializers.IntegerField()
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    employee_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False
    )
    shift_template_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False
    )
    constraints = serializers.JSONField(default=dict)
    preferences = serializers.JSONField(default=dict)
    # async 是 Python 關鍵字，以 run_async 作為欄位名稱；前端傳 "run_async": true
    run_async = serializers.BooleanField(default=False)

    # --- 新增：seed / drift mode ---
    seed_version_id = serializers.IntegerField(required=False, allow_null=True)
    minimize_drift_from_seed = serializers.BooleanField(default=False)
    time_decay_n = serializers.IntegerField(default=14)
    today = serializers.DateField(required=False, allow_null=True)
    drift_weight = serializers.IntegerField(default=10)

    # --- 新增：勞基法 toggle ---
    enforce_labor_law = serializers.BooleanField(default=False)

    # --- 新增：Token 計費 hook（第二階段才實作扣款）---
    consume_token = serializers.BooleanField(default=True)

    def validate(self, data):
        if data['period_start'] > data['period_end']:
            raise serializers.ValidationError("period_start must be before period_end")
        if data.get('minimize_drift_from_seed') and not data.get('seed_version_id'):
            raise serializers.ValidationError(
                "minimize_drift_from_seed=true requires seed_version_id"
            )
        return data


class ScheduleResultSerializer(serializers.Serializer):
    """排班結果序列化器"""
    success = serializers.BooleanField()
    assignments = serializers.ListField()
    score = serializers.SerializerMethodField()
    violations = serializers.ListField()
    metadata = serializers.DictField()
    message = serializers.CharField(required=False, allow_null=True)

    def get_score(self, obj):
        import math
        # obj may be a dataclass or a dict
        s = obj.score if hasattr(obj, 'score') else obj.get('score')
        if s is None or (isinstance(s, float) and not math.isfinite(s)):
            return None
        return s
