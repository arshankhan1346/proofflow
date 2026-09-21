from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from web3 import Web3
import os, json, time, hashlib, urllib.request
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="ProofFlow API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

RPC = os.getenv("RPC_URL")
KEY = os.getenv("PRIVATE_KEY")
ESCROW_ADDR = os.getenv("CONTRACT_ADDRESS")
TOKEN_ADDR = os.getenv("TOKEN_ADDRESS")
CHAIN_ID = 11155111
EXPLORER = "https://sepolia.etherscan.io"

w3 = Web3(Web3.HTTPProvider(RPC))
acct = w3.eth.account.from_key(KEY) if KEY else None

ESCROW_ABI = [
  {"name":"createProject","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"worker","type":"address"},{"name":"token","type":"address"},
             {"name":"descriptions","type":"string[]"},{"name":"amounts","type":"uint256[]"},
             {"name":"deadlines","type":"uint256[]"}],
   "outputs":[{"name":"projectId","type":"uint256"}]},
  {"name":"fundProject","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"projectId","type":"uint256"}],"outputs":[]},
  {"name":"submitEvidence","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"projectId","type":"uint256"},{"name":"milestoneId","type":"uint256"},
             {"name":"evidenceHash","type":"bytes32"}],"outputs":[]},
  {"name":"verifyMilestone","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"projectId","type":"uint256"},{"name":"milestoneId","type":"uint256"},
             {"name":"passed","type":"bool"},{"name":"verificationHash","type":"bytes32"}],"outputs":[]},
  {"name":"settleMilestone","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"projectId","type":"uint256"},{"name":"milestoneId","type":"uint256"}],"outputs":[]},
  {"name":"partialSettle","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"projectId","type":"uint256"},{"name":"milestoneId","type":"uint256"},
             {"name":"releaseAmount","type":"uint256"}],"outputs":[]},
  {"name":"raiseDispute","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"projectId","type":"uint256"},{"name":"milestoneId","type":"uint256"}],"outputs":[]},
  {"name":"resolveDispute","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"projectId","type":"uint256"},{"name":"milestoneId","type":"uint256"},
             {"name":"releaseAmount","type":"uint256"}],"outputs":[]},
  {"name":"getProject","type":"function","stateMutability":"view",
   "inputs":[{"name":"projectId","type":"uint256"}],
   "outputs":[{"name":"client","type":"address"},{"name":"worker","type":"address"},
              {"name":"token","type":"address"},{"name":"totalBudget","type":"uint256"},
              {"name":"totalFunded","type":"uint256"},{"name":"totalReleased","type":"uint256"},
              {"name":"status","type":"uint8"},{"name":"milestoneCount","type":"uint256"}]},
  {"name":"getMilestone","type":"function","stateMutability":"view",
   "inputs":[{"name":"projectId","type":"uint256"},{"name":"milestoneId","type":"uint256"}],
   "outputs":[{"name":"id","type":"uint256"},{"name":"description","type":"string"},
              {"name":"amount","type":"uint256"},{"name":"releasedAmount","type":"uint256"},
              {"name":"status","type":"uint8"},{"name":"verificationHash","type":"bytes32"},
              {"name":"evidenceHash","type":"bytes32"},{"name":"deadline","type":"uint256"}]},
  {"name":"getReputation","type":"function","stateMutability":"view",
   "inputs":[{"name":"user","type":"address"}],
   "outputs":[{"name":"milestones","type":"uint256"},{"name":"settledValue","type":"uint256"}]},
  {"name":"nextProjectId","type":"function","stateMutability":"view","inputs":[],
   "outputs":[{"name":"","type":"uint256"}]},
]

ERC20_ABI = [
  {"name":"approve","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
   "outputs":[{"name":"","type":"bool"}]},
  {"name":"balanceOf","type":"function","stateMutability":"view",
   "inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
  {"name":"mint","type":"function","stateMutability":"nonpayable",
   "inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[]},
]

escrow = w3.eth.contract(address=Web3.to_checksum_address(ESCROW_ADDR), abi=ESCROW_ABI) if ESCROW_ADDR else None
token  = w3.eth.contract(address=Web3.to_checksum_address(TOKEN_ADDR),  abi=ERC20_ABI)  if TOKEN_ADDR  else None

MS = ["CREATED","FUNDED","SUBMITTED","VERIFYING","VERIFIED","REJECTED","RELEASED","DISPUTED"]
PS = ["CREATED","FUNDED","ACTIVE","COMPLETED","DISPUTED","CANCELLED"]

STORE = {"projects": {}, "audit": {}}

def audit(pid, action, detail=None, tx=None):
    STORE["audit"].setdefault(str(pid), []).append(
        {"time": time.strftime("%H:%M:%S"), "action": action, "detail": detail, "tx": tx})

def send(fn):
    tx = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": CHAIN_ID,
        "maxFeePerGas": w3.to_wei("10", "gwei"),
        "maxPriorityFeePerGas": w3.to_wei("2", "gwei"),
    })
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(h, timeout=240)
    if receipt.status != 1:
        raise Exception(f"Transaction reverted: {EXPLORER}/tx/{h.hex()}")
    return h.hex(), receipt

def http_status(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ProofFlow-Verification"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status

# ---------- Verification Engine ----------

def run_checks(pid, mid, ev):
    checks = []
    m = escrow.functions.getMilestone(pid, mid).call()
    dl, now = m[7], int(time.time())
    ok = (dl == 0 or dl > now)
    checks.append({"check": "deadline_valid", "passed": ok,
                   "detail": "deadline ok" if ok else "deadline expired"})
    repo = ev.get("repo", "")
    if repo:
        try:
            ok = http_status(f"https://api.github.com/repos/{repo}") == 200
        except Exception:
            ok = False
        checks.append({"check": "github_repo_exists", "passed": ok, "detail": repo})
        if ev.get("commit"):
            try:
                ok = http_status(f"https://api.github.com/repos/{repo}/commits/{ev['commit']}") == 200
            except Exception:
                ok = False
            checks.append({"check": "commit_exists", "passed": ok, "detail": ev["commit"][:10]})
        for f in ev.get("files", []):
            try:
                ok = http_status(f"https://api.github.com/repos/{repo}/contents/{f}") == 200
            except Exception:
                ok = False
            checks.append({"check": f"file_exists:{f}", "passed": ok, "detail": f})
    if ev.get("deployment_url"):
        try:
            ok = http_status(ev["deployment_url"]) < 500
        except Exception:
            ok = False
        checks.append({"check": "deployment_healthy", "passed": ok, "detail": ev["deployment_url"]})
    return checks

def ai_analyze(checks, description):
    total = len(checks)
    passedn = sum(1 for c in checks if c["passed"])
    rate = passedn / total if total else 0.0
    score = int(rate * 100)
    conf = round(0.95 if rate >= 0.99 else (0.8 if rate >= 0.7 else 0.45), 2)
    rec = "PASS" if (score >= 80 and conf >= 0.8) else ("HOLD" if score >= 50 else "FAIL")
    return {"milestone": description, "completion_score": score,
            "requirements": {c["check"]: c["passed"] for c in checks},
            "confidence": conf,
            "risk_flags": [c["check"] for c in checks if not c["passed"]],
            "recommendation": rec}

def policy(checks, ai):
    reasons = []
    decision = "PASS"
    total = len(checks)
    rate = sum(1 for c in checks if c["passed"]) / total if total else 0
    if ai["confidence"] < 0.8:
        reasons.append("AI confidence below 0.8"); decision = "HOLD"
    if ai["completion_score"] < 80:
        reasons.append("completion score below 80")
        decision = "FAIL" if ai["completion_score"] < 50 else "HOLD"
    if rate < 0.7:
        reasons.append("deterministic pass rate below 70%"); decision = "FAIL"
    return {"decision": decision, "reasons": reasons,
            "deterministic_pass_rate": round(rate, 2),
            "ai_confidence": ai["confidence"],
            "completion_score": ai["completion_score"]}

# ---------- API Routes ----------

class EvidenceIn(BaseModel):
    repo: str
    commit: Optional[str] = ""
    files: List[str] = []
    deployment_url: Optional[str] = ""
    note: Optional[str] = ""

class AmountIn(BaseModel):
    amount: float

@app.get("/")
def root():
    p = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
    if os.path.exists(p):
        return FileResponse(p)
    return {"message": "ProofFlow backend running", "connected": w3.is_connected()}

@app.get("/contract")
def contract_info():
    code = w3.eth.get_code(Web3.to_checksum_address(ESCROW_ADDR)) if ESCROW_ADDR else b""
    return {"escrow": ESCROW_ADDR, "token": TOKEN_ADDR, "is_deployed": len(code) > 0,
            "connected": w3.is_connected(),
            "backend_wallet": acct.address if acct else None}

@app.post("/demo/load")
def demo_load():
    pid = escrow.functions.nextProjectId().call()
    now = int(time.time())
    descs = ["UI Implementation", "Backend Authentication", "Automated Testing", "Deployment"]
    amounts = [u * 10**6 for u in [100, 150, 100, 150]]
    deadlines = [now + 86400*7, now + 86400*14, now + 86400*21, now + 86400*28]
    tx1, _ = send(escrow.functions.createProject(acct.address, token.address, descs, amounts, deadlines))
    tx2, _ = send(token.functions.approve(escrow.address, sum(amounts)))
    tx3, _ = send(escrow.functions.fundProject(pid))
    STORE["projects"][str(pid)] = {"name": "Build Authentication System", "evidence": {}, "verifications": {}}
    audit(pid, "Project created on-chain", tx=tx1)
    audit(pid, "500 pfUSD approved for escrow", tx=tx2)
    audit(pid, "Funds deposited - 500 pfUSD LOCKED", tx=tx3)
    return {"project_id": pid, "tx_create": tx1, "tx_approve": tx2, "tx_fund": tx3}

@app.get("/projects/{pid}")
def get_project(pid: int):
    p = escrow.functions.getProject(pid).call()
    miles = []
    for i in range(p[7]):
        m = escrow.functions.getMilestone(pid, i).call()
        miles.append({"index": i, "description": m[1], "amount": m[2]/1e6,
                      "released": m[3]/1e6, "status": MS[m[4]],
                      "evidence_hash": m[6].hex() if isinstance(m[6], (bytes, bytearray)) else m[6],
                      "deadline": m[7]})
    meta = STORE["projects"].get(str(pid), {})
    locked = token.functions.balanceOf(escrow.address).call()/1e6 if token else 0
    return {"project_id": pid, "name": meta.get("name", "On-chain project"),
            "client": p[0], "worker": p[1], "total_budget": p[3]/1e6,
            "total_funded": p[4]/1e6, "total_released": p[5]/1e6,
            "status": PS[p[6]], "locked_in_escrow": locked, "milestones": miles,
            "evidence": meta.get("evidence", {}), "verifications": meta.get("verifications", {})}

@app.post("/projects/{pid}/milestones/{mid}/evidence")
def submit_evidence(pid: int, mid: int, ev: EvidenceIn):
    data = ev.model_dump()
    canon = json.dumps(data, sort_keys=True, separators=(",", ":"))
    h = "0x" + hashlib.sha256(canon.encode()).hexdigest()
    tx, _ = send(escrow.functions.submitEvidence(pid, mid, bytes.fromhex(h[2:])))
    meta = STORE["projects"].setdefault(str(pid), {"name": "", "evidence": {}, "verifications": {}})
    meta["evidence"][str(mid)] = {"hash": h, "data": data, "tx": tx}
    audit(pid, f"Evidence submitted for milestone {mid}", detail=h, tx=tx)
    return {"evidence_hash": h, "tx": tx}

@app.post("/projects/{pid}/milestones/{mid}/verify")
def verify(pid: int, mid: int):
    meta = STORE["projects"].get(str(pid))
    ev = meta["evidence"].get(str(mid)) if meta else None
    if not ev:
        return {"error": "no evidence submitted yet"}
    m = escrow.functions.getMilestone(pid, mid).call()
    checks = run_checks(pid, mid, ev["data"])
    ai = ai_analyze(checks, m[1])
    pol = policy(checks, ai)
    if pol["decision"] == "HOLD":
        audit(pid, f"Milestone {mid} ON HOLD by policy engine", detail=pol["reasons"])
        return {"deterministic_checks": checks, "ai_analysis": ai,
                "policy_result": pol, "final_decision": "HOLD", "tx": None}
    passed = pol["decision"] == "PASS"
    vh = w3.keccak(text=json.dumps({"checks": checks, "ai": ai, "policy": pol}, sort_keys=True))
    tx, _ = send(escrow.functions.verifyMilestone(pid, mid, passed, vh))
    meta["verifications"][str(mid)] = {"checks": checks, "ai": ai, "policy": pol,
                                       "decision": pol["decision"], "tx": tx}
    audit(pid, f"Milestone {mid} {'VERIFIED' if passed else 'REJECTED'} by policy engine",
          detail=f"score {ai['completion_score']}%, confidence {ai['confidence']}", tx=tx)
    return {"deterministic_checks": checks, "ai_analysis": ai, "policy_result": pol,
            "final_decision": pol["decision"], "tx": tx}

@app.post("/projects/{pid}/milestones/{mid}/settle")
def settle(pid: int, mid: int):
    tx, _ = send(escrow.functions.settleMilestone(pid, mid))
    m = escrow.functions.getMilestone(pid, mid).call()
    audit(pid, f"Milestone {mid} SETTLED - {m[3]/1e6} pfUSD released to worker", tx=tx)
    return {"tx": tx, "released": m[3]/1e6, "status": "CONFIRMED"}

@app.post("/projects/{pid}/milestones/{mid}/settle-partial")
def settle_partial(pid: int, mid: int, body: AmountIn):
    amt = int(body.amount * 10**6)
    tx, _ = send(escrow.functions.partialSettle(pid, mid, amt))
    audit(pid, f"Milestone {mid} PARTIAL settlement - {body.amount} pfUSD released", tx=tx)
    return {"tx": tx, "released": body.amount}

@app.post("/projects/{pid}/milestones/{mid}/dispute")
def dispute(pid: int, mid: int):
    tx, _ = send(escrow.functions.raiseDispute(pid, mid))
    audit(pid, f"Milestone {mid} DISPUTED by worker", tx=tx)
    return {"tx": tx, "status": "DISPUTED"}

@app.post("/projects/{pid}/milestones/{mid}/resolve")
def resolve(pid: int, mid: int, body: AmountIn):
    amt = int(body.amount * 10**6)
    tx, _ = send(escrow.functions.resolveDispute(pid, mid, amt))
    audit(pid, f"Dispute resolved - {body.amount} pfUSD released", tx=tx)
    return {"tx": tx}

@app.get("/users/{addr}/reputation")
def reputation(addr: str):
    rep = escrow.functions.getReputation(Web3.to_checksum_address(addr)).call()
    return {"address": addr, "milestones_completed": rep[0], "total_settled_pfusd": rep[1]/1e6}

@app.get("/audit/{pid}")
def audit_log(pid: int):
    return STORE["audit"].get(str(pid), [])

@app.get("/balances")
def balances():
    return {"backend_wallet": acct.address,
            "wallet_pfusd": token.functions.balanceOf(acct.address).call()/1e6,
            "escrow_pfusd": token.functions.balanceOf(escrow.address).call()/1e6}