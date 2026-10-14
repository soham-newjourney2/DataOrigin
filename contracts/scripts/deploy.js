// Deploys ProvenanceRegistry and writes {address, abi, chainId} for the backend.
// Usage: npx hardhat run scripts/deploy.js --network localhost
import { writeFileSync } from "node:fs";
import hre, { network } from "hardhat";

const OUT = new URL("../../backend/app/chain_deployment.json", import.meta.url);

const { ethers } = await network.getOrCreate();
const registry = await ethers.deployContract("ProvenanceRegistry");
await registry.waitForDeployment();
const address = await registry.getAddress();
const { abi } = await hre.artifacts.readArtifact("ProvenanceRegistry");
const chainId = Number((await ethers.provider.getNetwork()).chainId);

writeFileSync(OUT, JSON.stringify({ address, abi, chainId }, null, 2) + "\n");
console.log(`ProvenanceRegistry deployed at ${address} (chainId ${chainId}) → backend/app/chain_deployment.json`);
