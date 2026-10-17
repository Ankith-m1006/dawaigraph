"""Family medicine memory, kept in the same graph as the medicine data.

    (:Person {id, name, age})-[:HAS_RX]->(:Rx {id, date, note})<-[:WROTE]-(:Doctor {id, name, speciality})
    (:Rx)-[:LISTS {dose}]->(:Brand)
    (:Person)-[:TAKES {since, until, rx}]->(:Brand)               until = '' while active
    (:Person)-[:HAS_FINDING]->(:Finding {key, type, level, summary, status, raised, updated})
    (:Finding)-[:ABOUT]->(:Brand)
    (:Carer {id, name})-[:CARES_FOR]->(:Person)
    (:Carer)-[:UPDATED {at, status, note}]->(:Finding)                who changed what, and when

A finding's key is derived from what it is about, so the same problem found again
in a later session updates the existing node instead of being raised twice.

Every family is a tenant. Its Person, Carer, Doctor, Rx and Finding nodes carry `tenant`
and ids prefixed with "<tenant>/", and every memory query is scoped to one tenant, so one
family never sees another's medicines. The medicine catalogue (Brand, Salt, DrugClass) is
shared: a family's TAKES edges have to reach the same Brand nodes, which is why families
are separated inside one graph rather than kept in separate graphs.
"""
import datetime
import hashlib
import re


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def doctor_id(name):
    """'Dr. S. Shetty' and 'Dr Shetty' are the same prescriber: drop titles, initials and punctuation."""
    words = [w for w in re.findall(r"[a-z]+", name.lower()) if w not in {"dr", "doctor", "prof", "mr", "mrs", "ms"} and len(w) > 1]
    return "d-" + "-".join(words or ["unknown"])


def finding_key(kind, *parts):
    raw = kind + "|" + "|".join(sorted(str(p) for p in parts))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


TENANT_LABELS = ("Finding", "Rx", "Doctor", "Carer", "Person")


def scoped(tenant, local_id):
    """A tenant's id for a node: 'fab12cd/p-amma'. Ids that are already scoped are kept."""
    return local_id if local_id.startswith(tenant + "/") else f"{tenant}/{local_id}"


class Memory:
    def __init__(self, graph, tenant=None):
        self.g = graph
        self.tenant = tenant

    def for_tenant(self, tenant):
        return Memory(self.g, tenant)

    def owns(self, node_id):
        """True when an id belongs to this tenant. Every id that comes from a client or the model is checked."""
        return bool(self.tenant) and isinstance(node_id, str) and node_id.startswith(self.tenant + "/")

    def people(self):
        res = self.g.ro_query(
            "MATCH (p:Person {tenant: $t}) OPTIONAL MATCH (c:Carer {tenant: $t})-[:CARES_FOR]->(p) "
            "RETURN p.id, p.name, collect({id: c.id, name: c.name}) ORDER BY p.name",
            {"t": self.tenant},
        )
        return [{"id": r[0], "name": r[1], "carers": [c for c in r[2] if c.get("id")]} for r in res.result_set]

    def count_people(self):
        return self.g.ro_query("MATCH (p:Person {tenant: $t}) RETURN count(p)", {"t": self.tenant}).result_set[0][0]

    def delete_tenant(self, tenant=None):
        for label in TENANT_LABELS:
            self.g.query(f"MATCH (n:{label} {{tenant: $t}}) DETACH DELETE n", {"t": tenant or self.tenant})

    def ensure_schema(self):
        for q in (
            "CREATE INDEX FOR (p:Person) ON (p.id)",
            "CREATE INDEX FOR (d:Doctor) ON (d.id)",
            "CREATE INDEX FOR (r:Rx) ON (r.id)",
            "CREATE INDEX FOR (f:Finding) ON (f.key)",
            "CREATE INDEX FOR (c:Carer) ON (c.id)",
            "CREATE INDEX FOR (p:Person) ON (p.tenant)",
            "CREATE INDEX FOR (r:Rx) ON (r.tenant)",
            "CREATE INDEX FOR (d:Doctor) ON (d.tenant)",
            "CREATE INDEX FOR (c:Carer) ON (c.tenant)",
            "CREATE INDEX FOR (f:Finding) ON (f.tenant)",
            "CREATE INDEX FOR (x:Tenant) ON (x.id)",
        ):
            try:
                self.g.query(q)
            except Exception as e:
                if "already" not in str(e).lower():
                    raise

    def add_person(self, pid, name, age=None, carers=()):
        pid = scoped(self.tenant, pid)
        self.g.query("MERGE (p:Person {id: $id}) SET p.name = $name, p.age = $age, p.tenant = $t",
                     {"id": pid, "name": name, "age": age, "t": self.tenant})
        for cid, cname in carers:
            self.g.query(
                "MATCH (p:Person {id: $pid}) MERGE (c:Carer {id: $cid}) SET c.name = $cname, c.tenant = $t "
                "MERGE (c)-[:CARES_FOR]->(p)",
                {"pid": pid, "cid": scoped(self.tenant, cid), "cname": cname, "t": self.tenant},
            )
        return pid

    def add_prescription(self, pid, rx_id, date, doctor, items, note=""):
        """items: [{brand_id, dose}]; doctor: {id, name, speciality}."""
        pid, rx_id = scoped(self.tenant, pid), scoped(self.tenant, rx_id)
        self.g.query(
            "MATCH (p:Person {id: $pid, tenant: $t}) "
            "MERGE (d:Doctor {id: $did}) SET d.name = $dname, d.speciality = $spec, d.tenant = $t "
            "MERGE (r:Rx {id: $rx}) SET r.date = $date, r.note = $note, r.tenant = $t "
            "MERGE (d)-[:WROTE]->(r) MERGE (p)-[:HAS_RX]->(r)",
            {"pid": pid, "did": scoped(self.tenant, doctor["id"]), "dname": doctor["name"],
             "spec": doctor.get("speciality", ""), "rx": rx_id, "date": date, "note": note, "t": self.tenant},
        )
        self.g.query(
            "MATCH (p:Person {id: $pid}), (r:Rx {id: $rx}) UNWIND $items AS it "
            "MATCH (b:Brand {id: it.brand_id}) "
            "MERGE (r)-[l:LISTS]->(b) SET l.dose = it.dose "
            "MERGE (p)-[t:TAKES {rx: $rx}]->(b) ON CREATE SET t.since = $date, t.until = ''",
            {"pid": pid, "rx": rx_id, "items": items, "date": date},
        )

    def stop(self, pid, brand_id, date):
        self.g.query(
            "MATCH (:Person {id: $pid, tenant: $tn})-[t:TAKES]->(:Brand {id: $bid}) WHERE t.until = '' SET t.until = $date",
            {"pid": pid, "bid": brand_id, "date": date, "tn": self.tenant},
        )

    def active(self, pid):
        res = self.g.ro_query(
            "MATCH (p:Person {id: $pid, tenant: $tn})-[t:TAKES]->(b:Brand) WHERE t.until = '' "
            "MATCH (r:Rx {id: t.rx})<-[:WROTE]-(d:Doctor) "
            "RETURN b.id, b.name, d.name, d.speciality, t.since ORDER BY t.since",
            {"pid": pid, "tn": self.tenant},
        )
        return [dict(zip(("brand_id", "brand", "doctor", "speciality", "since"), r)) for r in res.result_set]

    def record(self, pid, findings):
        """findings: [{key, type, level, summary, brand_ids}]. Returns which were new and which were already known."""
        new, known = [], []
        for f in findings:
            res = self.g.query(
                "MATCH (p:Person {id: $pid, tenant: $t}) "
                "MERGE (p)-[:HAS_FINDING]->(x:Finding {key: $key}) "
                "ON CREATE SET x.tenant = $t, x.type = $type, x.level = $level, x.summary = $summary, x.status = 'open', "
                "x.raised = $now, x.updated = $now, x.fresh = true "
                "ON MATCH SET x.fresh = false "
                "WITH x UNWIND $brands AS bid MATCH (b:Brand {id: bid}) MERGE (x)-[:ABOUT]->(b) "
                "RETURN DISTINCT x.fresh, x.status, x.updated",
                {"pid": pid, "t": self.tenant, "key": f["key"], "type": f["type"], "level": f.get("level", ""),
                 "summary": f["summary"], "brands": f["brand_ids"], "now": _now()},
            )
            if not res.result_set:
                continue  # a person of another tenant: nothing is written
            fresh, status, updated = res.result_set[0]
            (new if fresh else known).append({**f, "status": status, "updated": updated})
        return new, known

    def update_finding(self, pid, key, status, carer_id, note=""):
        self.g.query(
            "MATCH (:Person {id: $pid, tenant: $t})-[:HAS_FINDING]->(x:Finding {key: $key}), (c:Carer {id: $cid, tenant: $t}) "
            "SET x.status = $status, x.updated = $now "
            "CREATE (c)-[:UPDATED {at: $now, status: $status, note: $note}]->(x)",
            {"pid": pid, "key": key, "status": status, "cid": carer_id, "note": note, "now": _now(), "t": self.tenant},
        )

    def history(self, pid):
        res = self.g.ro_query(
            "MATCH (:Person {id: $pid, tenant: $t})-[:HAS_FINDING]->(x:Finding) "
            "OPTIONAL MATCH (c:Carer {tenant: $t})-[u:UPDATED]->(x) "
            "RETURN x.key, x.type, x.summary, x.status, x.raised, collect([c.name, u.status, u.at, u.note]) "
            "ORDER BY x.raised",
            {"pid": pid, "t": self.tenant},
        )
        return [dict(zip(("key", "type", "summary", "status", "raised", "updates"), r)) for r in res.result_set]
