"""Create explicit demo sessions through the same API used by the participant app."""
import argparse
import json
import random
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

def call(base, path, payload=None, token=None):
    headers = {"Content-Type":"application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if payload is None else json.dumps(payload).encode()
    with urlopen(Request(base + path, data=data, headers=headers), timeout=15) as response:
        return json.load(response)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",default="http://127.0.0.1:8765")
    parser.add_argument("--count",type=int,default=18)
    parser.add_argument("--seed",type=int,default=42)
    args=parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error("count must be between 1 and 100")
    scenarios=call(args.url,"/api/v1/scenarios")["scenarios"]
    randomizer=random.Random(args.seed)
    for index in range(args.count):
        scenario=scenarios[index%len(scenarios)]
        namespace=f"blockwise-demo-v1:{args.seed}:{index}:{scenario['id']}:{scenario['version']}"
        identity=lambda stage: str(uuid5(NAMESPACE_URL, namespace+":"+stage))
        constraints=dict(scenario["defaults"])
        constraints["budget_cents"] += randomizer.choice([0,500,1000])
        constraints["time_budget_minutes"] += randomizer.choice([0,10,20])
        session=call(args.url,"/api/v1/sessions",{"scenario_id":scenario["id"],"constraints":constraints,"is_demo":True,"consent":True,"request_id":identity("session")})
        path="/api/v1/sessions/"+session["id"]
        store=randomizer.choice(scenario["stores"])
        assignments={item["id"]:store["id"] for item in scenario["basket"]}
        call(args.url,path+"/choices",{"stage":"initial","assignments":assignments,"request_id":identity("initial")},session["token"])
        options=call(args.url,path+"/recommendations",{},session["token"])
        if index%4 and options["plans"]:
            plan=options["plans"][0 if index%3 else -1]
            call(args.url,path+"/choices",{"stage":"revised","assignments":plan["assignments"],"request_id":identity("revised")},session["token"])
    print(f"Prepared {args.count} explicitly labeled demo sessions (safe to rerun with identical seed).")
if __name__=="__main__":
    main()
