const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying test token with:", deployer.address);

  const Token = await hre.ethers.getContractFactory("MockERC20");
  const token = await Token.deploy("ProofFlow Test USD", "pfUSD", 6);
  await token.waitForDeployment();

  const addr = await token.getAddress();
  console.log("✅ pfUSD token deployed to:", addr);

  // Mint 10,000 test dollars for the demo
  const tx = await token.mint(deployer.address, 10000n * 1000000n);
  await tx.wait();
  console.log("Minted 10,000 pfUSD to deployer");
}

main().catch((e) => { console.error(e); process.exitCode = 1; });