# Alerts

Written record of things that got worse, what caught them, and what changed.

One file per alert, each covering: what got worse, how it was detected, what the
impact was, what changed as a result, and which eval case now covers it.

Alerts where the failure was caught before deploy are as valuable to record
as ones that reached production — arguably more so, since they document the
detection working.

Written by `obs/alerts.py`. A repeat alert on the same day, for the same kind
and subject, appends a timestamped occurrence to the file that already exists
rather than creating a new one.

*Empty.*
