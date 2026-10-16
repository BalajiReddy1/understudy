from concurrent.futures import ThreadPoolExecutor

from understudy.db import copy, drop


def test_many_copies_at_once(db):
    # On 4.22 GRAPH.COPY forks the server, so parallel copies used to fail with
    # "could not fork". The helper waits and retries instead.
    drop(db, "test:copy-src")
    src = db.select_graph("test:copy-src")
    src.query("UNWIND range(1, 2000) AS i CREATE (:N {i: i})")
    names = [f"test:copy{i}" for i in range(6)]
    for n in names:
        drop(db, n)
    with ThreadPoolExecutor(6) as pool:
        copies = list(pool.map(lambda n: copy(src, n), names))
    for c in copies:
        assert c.ro_query("MATCH (n:N) RETURN count(n)").result_set[0][0] == 2000
        c.delete()
    src.delete()
