TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_catalog",
            "description": (
                "List cataloged signals/KPIs. Use this to discover what signals exist before "
                "querying their readings, or to answer 'what does X measure?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_filter": {
                        "type": "string",
                        "description": "ISA-95 topic prefix to filter by, e.g. 'plant1.line3'. Omit to list everything.",
                    },
                    "signal_type": {
                        "type": "string",
                        "enum": ["raw", "kpi"],
                        "description": "Restrict to raw physical signals or computed KPIs. Omit for both.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_latest_value",
            "description": "Get the current value of one specific signal. Use for 'what is X right now?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "The signal's ISA-95 topic (without any suffix)."},
                    "signal_key": {"type": "string", "description": "The signal's key within that topic."},
                },
                "required": ["topic", "signal_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_readings",
            "description": (
                "Get the historical time series of one signal between two instants, with optional aggregation. "
                "Use agg='1m' or agg='1h' for wide time ranges; agg='raw' is capped to a short window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "The signal's ISA-95 topic (without any suffix)."},
                    "signal_key": {"type": "string", "description": "The signal's key within that topic."},
                    "from_time": {"type": "string", "format": "date-time", "description": "Start of the range, ISO 8601."},
                    "to_time": {"type": "string", "format": "date-time", "description": "End of the range, ISO 8601."},
                    "agg": {
                        "type": "string",
                        "enum": ["raw", "1m", "1h"],
                        "description": "Aggregation level. 'raw' for individual samples (short ranges only), '1m'/'1h' for wider ranges.",
                    },
                },
                "required": ["topic", "signal_key", "from_time", "to_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "List discrete events (alarms, failures) for a topic within a time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_filter": {"type": "string", "description": "ISA-95 topic prefix to filter by. Omit for all topics."},
                    "event_key": {"type": "string", "description": "The event array's field name, e.g. 'alarms'. Omit for all."},
                    "from_time": {"type": "string", "format": "date-time", "description": "Start of the range, ISO 8601."},
                    "to_time": {"type": "string", "format": "date-time", "description": "End of the range, ISO 8601."},
                },
                "required": ["from_time", "to_time"],
            },
        },
    },
]
