"""Scheduling: turn an Intent Object into a feasible, timed Plan (§7.3).

The polished preset demos live in ``app.mock``; this package handles the live
free-text path (grounding + routing + time accumulation), degrading to clearly
labelled 示例 data when AMap is not configured."""
