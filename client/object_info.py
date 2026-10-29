"""What the sidebar and the top panel's hover text say about a dataset or model.

One description for both, so they never disagree: a title (the display name),
the kind (a dataset's ``datasetName``, a model's ``modelName``) and the
(label, value) lines the object reports.
"""
from html import escape


def describe(obj):
    """``(title, kind, [(label, value), ...])`` for a dataset or a model."""
    if hasattr(obj, "getBaseInfo"):
        return obj.getDisplayName(), obj.datasetName, obj.getBaseInfo() + obj.getInfo()
    return obj.getDisplayName(), obj.modelName, obj.getInfo()


def tooltipHtml(obj):
    """The description as hover text: name, kind, then one row per line."""
    title, kind, pairs = describe(obj)
    rows = "".join(
        f"<tr><td>{escape(str(label))}</td><td>&nbsp;&nbsp;{escape(str(value))}</td></tr>"
        for label, value in pairs
    )
    return f"<b>{escape(str(title))}</b><br><i>{escape(str(kind))}</i><table>{rows}</table>"
