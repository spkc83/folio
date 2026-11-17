"""Who proposed a change: every proposer of a proposal's lineage (four eyes).

Discovery and import write one row for each proposal; a rename copies the list forward
and adds the renamer. No one in the list may confirm or reject the proposal. The list is
never read from the free-text reason.
"""
import json

from folio_graph import Proposal

OWN = "The person who proposed a change cannot decide on it."


def proposers_of(store, p: Proposal) -> list[str]:
    """Everyone who proposed the content of ``p``, first proposer first.

    A proposal with no row has one proposer, its own.
    """
    row = store.row("SELECT proposers FROM proposal_lineage WHERE proposal_id = ?", (p.id,))
    return json.loads(row[0]) if row else [p.proposed_by]


def record(store, proposal_id: str, proposers: list[str]) -> None:
    store.execute("INSERT INTO proposal_lineage VALUES (?, ?)",
                  (proposal_id, json.dumps(list(dict.fromkeys(proposers)))))
