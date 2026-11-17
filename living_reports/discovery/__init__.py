"""Discovery: a catalog becomes proposals for the mapping layer (spec 7).

Five pure steps (candidates, the model's draft, changes, a preview model, probes) and a
run that proposes what survives. A person confirms on the model page.
"""
from living_reports.discovery.confirm import certify_metric, confirm_proposals, rename_proposal
from living_reports.discovery.probe import Probe
from living_reports.discovery.propose import Dropped
from living_reports.discovery.run import DiscoveryRun, discover

__all__ = [
    "DiscoveryRun",
    "Dropped",
    "Probe",
    "certify_metric",
    "confirm_proposals",
    "discover",
    "rename_proposal",
]
