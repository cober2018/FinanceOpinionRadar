待复核候选观点（JSON 数组，id 为观点标识）：
__VIEWPOINTS__

请逐条裁决，输出 JSON：{"reviews": [{"id": <原样回传>, "verdict": "accept"|"human", "reason": "...", "corrected_stance": "仅 accept 且需修正时给", "corrected_horizon": "仅 accept 且需修正时给"}]}
