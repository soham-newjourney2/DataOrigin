import { expect } from "chai";
import { network } from "hardhat";

const { ethers } = await network.getOrCreate();

describe("ProvenanceRegistry", function () {
  const hash = ethers.keccak256(ethers.toUtf8Bytes("dataset v1"));
  let registry, owner;

  beforeEach(async function () {
    [owner] = await ethers.getSigners();
    registry = await ethers.deployContract("ProvenanceRegistry");
  });

  it("registers a hash and emits Registered", async function () {
    expect(await registry.isRegistered(hash)).to.equal(false);
    await expect(registry.register(hash, "dataset_version", "1")).to.emit(registry, "Registered");
    expect(await registry.isRegistered(hash)).to.equal(true);
  });

  it("reverts on a duplicate hash", async function () {
    await registry.register(hash, "dataset_version", "1");
    await expect(registry.register(hash, "experiment", "2")).to.be.revertedWith("already registered");
  });

  it("getRecord returns the stored record", async function () {
    await registry.register(hash, "experiment", "7");
    const rec = await registry.getRecord(hash);
    expect(rec.hash).to.equal(hash);
    expect(rec.artifactType).to.equal("experiment");
    expect(rec.artifactId).to.equal("7");
    expect(rec.registrant).to.equal(owner.address);
    expect(rec.timestamp).to.be.greaterThan(0n);
  });

  it("getRecord of an unknown hash is empty", async function () {
    const rec = await registry.getRecord(ethers.ZeroHash);
    expect(rec.timestamp).to.equal(0n);
    expect(rec.registrant).to.equal(ethers.ZeroAddress);
  });
});
