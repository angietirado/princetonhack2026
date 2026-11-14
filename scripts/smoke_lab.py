"""Verify both local frontends and the full API flow without real participant data."""
import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from uuid import uuid4

def call(url,payload=None,token=None):
    headers={"Content-Type":"application/json"}
    if token:
        headers["Authorization"]="Bearer "+token
    with urlopen(Request(url,data=None if payload is None else json.dumps(payload).encode(),headers=headers),timeout=15) as response:
        return json.load(response)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend",default="http://127.0.0.1:8765")
    parser.add_argument("--participant",default="http://participant:8080")
    parser.add_argument("--analytics",default="http://analytics:8080")
    args=parser.parse_args()
    for url in [args.participant,args.analytics]:
        with urlopen(url,timeout=10) as response:
            html=response.read().decode()
            assert "Blockwise" in html
            assert response.headers.get("Content-Security-Policy")
        assert call(url+"/health")["status"]=="ok"
        for path in ["/.env","/.git/config","/not-an-asset.js"]:
            try:
                urlopen(url+path,timeout=10)
                raise AssertionError("Unexpectedly served "+path)
            except HTTPError as error:
                assert error.code==404
    scenario=call(args.backend+"/api/v1/scenarios")["scenarios"][0]
    session=call(args.backend+"/api/v1/sessions",{"scenario_id":scenario["id"],"constraints":scenario["defaults"],"is_demo":True,"consent":True,"request_id":str(uuid4())})
    path=args.backend+"/api/v1/sessions/"+session["id"]
    token=session["token"]
    assignments={item["id"]:scenario["stores"][0]["id"] for item in scenario["basket"]}
    initial={"stage":"initial","request_id":str(uuid4()),"assignments":assignments}
    saved=call(path+"/choices",initial,token)
    assert call(path+"/choices",initial,token)["id"]==saved["id"]
    plans=call(path+"/recommendations",{},token)["plans"]
    assert plans
    revised=call(path+"/choices",{"stage":"revised","request_id":str(uuid4()),"assignments":plans[0]["assignments"]},token)
    assert revised["evaluation"]["feasible"]
    restored=call(path,token=token)
    assert len(restored["choices"])==2
    demo=call(args.backend+"/api/v1/analytics/sessions?data_mode=demo")
    assert any(item["id"]==session["id"] for item in demo["sessions"])
    participant=call(args.backend+"/api/v1/analytics/sessions?data_mode=participants")
    assert all(item["id"]!=session["id"] for item in participant["sessions"])
    print("Both frontends and complete demo choice/recommendation/revision/analytics flow passed.")
if __name__=="__main__":
    main()
