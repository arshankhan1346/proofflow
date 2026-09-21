const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("ProofFlowEscrow", function () {
  let escrow, token;
  let admin, client, worker, verifier, arbitrator, stranger;

  const AMOUNTS = [
    ethers.parseUnits("100", 6),
    ethers.parseUnits("150", 6),
    ethers.parseUnits("100", 6),
    ethers.parseUnits("150", 6),
  ];
  const TOTAL = AMOUNTS.reduce((a, b) => a + b, 0n);

  beforeEach(async function () {
    [admin, client, worker, verifier, arbitrator, stranger] = await ethers.getSigners();

    const MockToken = await ethers.getContractFactory("MockERC20");
    token = await MockToken.deploy("Test USDC", "USDC", 6);
    await token.mint(client.address, ethers.parseUnits("10000", 6));

    const Escrow = await ethers.getContractFactory("ProofFlowEscrow");
    escrow = await Escrow.deploy();

    await escrow.grantRole(await escrow.VERIFIER_ROLE(), verifier.address);
    await escrow.grantRole(await escrow.ARBITRATOR_ROLE(), arbitrator.address);
  });

  async function createAndFund() {
    const now = Math.floor(Date.now() / 1000);
    const deadlines = AMOUNTS.map((_, i) => now + 86400 * (i + 7));
    await escrow.connect(client).createProject(
      worker.address,
      await token.getAddress(),
      ["UI", "Backend Auth", "Tests", "Deploy"],
      AMOUNTS,
      deadlines
    );
    await token.connect(client).approve(await escrow.getAddress(), TOTAL);
    await escrow.connect(client).fundProject(0);
  }

  const evHash = () => ethers.keccak256(ethers.toUtf8Bytes("evidence"));
  const verHash = () => ethers.keccak256(ethers.toUtf8Bytes("verification"));

  it("creates a project with milestones", async function () {
    const now = Math.floor(Date.now() / 1000);
    await escrow.connect(client).createProject(
      worker.address,
      await token.getAddress(),
      ["M1", "M2"],
      [AMOUNTS[0], AMOUNTS[1]],
      [now + 86400, now + 172800]
    );
    const p = await escrow.getProject(0);
    expect(p.client).to.equal(client.address);
    expect(p.worker).to.equal(worker.address);
    expect(p.totalBudget).to.equal(AMOUNTS[0] + AMOUNTS[1]);
  });

  it("funds the project and locks tokens", async function () {
    await createAndFund();
    const p = await escrow.getProject(0);
    expect(p.totalFunded).to.equal(TOTAL);
    expect(p.status).to.equal(2); // ACTIVE
  });

  it("worker submits evidence", async function () {
    await createAndFund();
    await escrow.connect(worker).submitEvidence(0, 0, evHash());
    const m = await escrow.getMilestone(0, 0);
    expect(m.status).to.equal(2); // SUBMITTED
    expect(m.evidenceHash).to.equal(evHash());
  });

  it("verifies and settles a milestone", async function () {
    await createAndFund();
    await escrow.connect(worker).submitEvidence(0, 0, evHash());
    await escrow.connect(verifier).verifyMilestone(0, 0, true, verHash());
    await escrow.connect(verifier).settleMilestone(0, 0);
    const m = await escrow.getMilestone(0, 0);
    expect(m.status).to.equal(6); // RELEASED
    expect(m.releasedAmount).to.equal(AMOUNTS[0]);
  });

  it("rejects double settlement", async function () {
    await createAndFund();
    await escrow.connect(worker).submitEvidence(0, 0, evHash());
    await escrow.connect(verifier).verifyMilestone(0, 0, true, verHash());
    await escrow.connect(verifier).settleMilestone(0, 0);
    await expect(escrow.connect(verifier).settleMilestone(0, 0))
      .to.be.revertedWithCustomError(escrow, "InvalidState");
  });

  it("rejects unauthorized settlement", async function () {
    await createAndFund();
    await expect(escrow.connect(stranger).settleMilestone(0, 0)).to.be.reverted;
  });

  it("supports partial settlement", async function () {
    await createAndFund();
    await escrow.connect(worker).submitEvidence(0, 0, evHash());
    await escrow.connect(verifier).verifyMilestone(0, 0, true, verHash());
    await escrow.connect(verifier).partialSettle(0, 0, ethers.parseUnits("87", 6));
    const m = await escrow.getMilestone(0, 0);
    expect(m.releasedAmount).to.equal(ethers.parseUnits("87", 6));
    expect(m.status).to.equal(4); // still VERIFIED, not fully released
  });

  it("handles dispute and resolution", async function () {
    await createAndFund();
    await escrow.connect(worker).submitEvidence(0, 0, evHash());
    await escrow.connect(verifier).verifyMilestone(0, 0, false, verHash());
    await escrow.connect(worker).raiseDispute(0, 0);
    let m = await escrow.getMilestone(0, 0);
    expect(m.status).to.equal(7); // DISPUTED
    await escrow.connect(arbitrator).resolveDispute(0, 0, ethers.parseUnits("50", 6));
    m = await escrow.getMilestone(0, 0);
    expect(m.releasedAmount).to.equal(ethers.parseUnits("50", 6));
    expect(m.status).to.equal(6); // RELEASED
  });

  it("rejects evidence from wrong wallet", async function () {
    await createAndFund();
    await expect(escrow.connect(client).submitEvidence(0, 0, evHash()))
      .to.be.revertedWithCustomError(escrow, "Unauthorized");
  });

  it("tracks on-chain reputation", async function () {
    await createAndFund();
    await escrow.connect(worker).submitEvidence(0, 0, evHash());
    await escrow.connect(verifier).verifyMilestone(0, 0, true, verHash());
    await escrow.connect(verifier).settleMilestone(0, 0);
    const rep = await escrow.getReputation(worker.address);
    expect(rep.milestones).to.equal(1n);
    expect(rep.settledValue).to.equal(AMOUNTS[0]);
  });

  it("rejects verification of unsubmitted milestone", async function () {
    await createAndFund();
    await expect(escrow.connect(verifier).verifyMilestone(0, 0, true, verHash()))
      .to.be.revertedWithCustomError(escrow, "InvalidState");
  });
});