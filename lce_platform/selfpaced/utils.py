import json


def safe_json(data) -> str:
    """
    Serialize data to JSON safe for embedding in HTML <script> blocks.
    Escapes <, >, & to Unicode escapes so content can't break out of a script tag.
    Equivalent to what Django's json_script filter does internally.
    """
    return (
        json.dumps(data)
        .replace('<', '\\u003C')
        .replace('>', '\\u003E')
        .replace('&', '\\u0026')
    )
