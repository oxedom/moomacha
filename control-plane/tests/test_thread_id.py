import uuid
from control_plane.services.job_queue import Job
from control_plane.runtime.runners.thread_id import make_thread_id


class _Agent:
    def __init__(self, aid):
        self.id = aid


def test_stream_thread_id():
    aid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    job = Job(agent_id=aid, channel="sandbox", topic="daily-priorities", content="hi")
    assert make_thread_id(job, _Agent(aid)) == f"zulip:stream:sandbox:daily-priorities:{aid}"


def test_direct_thread_id_is_sorted_and_stable():
    aid = uuid.UUID("00000000-0000-0000-0000-000000000002")
    job = Job(
        agent_id=aid, channel="", topic="", content="hi",
        conversation_type="direct", direct_recipient_ids=[1085236, 108421],
    )
    assert make_thread_id(job, _Agent(aid)) == f"zulip:direct:108421,1085236:{aid}"
