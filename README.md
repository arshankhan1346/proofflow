<p align="center">
  <img src="https://img.shields.io/badge/Network-Sepolia%20Testnet-blue?style=for-the-badge&logo=ethereum" />
  <img src="https://img.shields.io/badge/AI-Policy%20Engine-purple?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Smart%20Contract-Solidity-363636?style=for-the-badge&logo=solidity" />
</p>

<h1 align="center">🛡️ ProofFlow: AI-Verified Blockchain Escrow</h1>
   <p align="center">
     <img src="https://img.shields.io/badge/License-MIT-yellow.svg" />
   </p>
<p align="center">
  <b>The world's first trustless escrow protocol that uses AI to verify real-world deliverables before releasing smart contract payments.</b>
</p>

---

## 🚨 The Problem
Freelance platforms take **20% fees** and rely on **slow, biased human arbitrators** to resolve disputes. Smart contracts can hold money, but they are "blind"—they cannot see if a GitHub repository actually works or if a website is actually deployed.

## 💡 The ProofFlow Solution
ProofFlow bridges the gap between Web3 and the real world. 
1. 💰 **Client** locks funds (pfUSD) in a Smart Contract.
2. 📦 **Worker** submits cryptographic evidence (GitHub repo, commit hash, live URL).
3. 🧠 **AI Policy Engine** queries the open web (GitHub API, live pings) to verify the work.
4. ⚖️ **Smart Contract** automatically releases funds if the AI votes `PASS`, or blocks them if the AI votes `FAIL`.

---

## 🎥 Demo Video
> 🚧 *[Paste your YouTube or Loom video link here after you record it!]*

---

## 🏗️ System Architecture

```text
┌─────────────────────┐     ┌───────────────────────────────┐     ┌─────────────────────┐
│      Frontend       │     │          Backend              │     │    Blockchain       │
│ (HTML/Tailwind/JS)  │<───>│ (Python / FastAPI / Web3.py)  │<───>│ (Solidity / Sepolia)│
└─────────────────────┘     └───────────────────────────────┘     └─────────────────────┘
         │                          │                                   │
         │ 1. Submit Evidence       │ 2. Verify with AI                 │ 3. Store SHA-256 Hash
         │─────────────────────────>│──────────────────────────────────>│
         │                          │                                   │
         │ 4. Run AI Verify         │ 5. GitHub API + Web Scraping      │ 6. Update Milestone State
         │─────────────────────────>│──────────────────────────────────>│
         │                          │                                   │
         │ 7. Settle                │ 8. Release pfUSD                  │ 9. Transfer Tokens
         │─────────────────────────>│──────────────────────────────────>│
