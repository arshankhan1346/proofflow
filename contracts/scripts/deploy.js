const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);

  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log("Account balance (ETH):", hre.ethers.formatEther(balance));

  const Escrow = await hre.ethers.getContractFactory("ProofFlowEscrow");
  const escrow = await Escrow.deploy();
  await escrow.waitForDeployment();

  console.log("");
  console.log("✅ ProofFlowEscrow deployed to:", await escrow.getAddress());
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});