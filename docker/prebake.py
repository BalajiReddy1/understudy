"""Run once while the image builds, against a FalkorDB started for it: load the org from
the snapshot and build the sample org, numbers and all. The build then saves FalkorDB's
data file into the image, so starting a container only reads it back. On a free host's
tenth of a CPU, working the numbers out at start took over a minute."""

from understudy import snapshot
from understudy.config import SNAPSHOT
from understudy.db import connect
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.refresh import refresh
from understudy.sample import SAMPLE_ORG

db = connect()
snapshot.load(db, "org", SNAPSHOT)
write_org(db.select_graph("sample"), SAMPLE_ORG)
build_flow_layer(db.select_graph("sample"))
refresh(db, "sample")
print("prebaked", sorted(db.list_graphs()))
