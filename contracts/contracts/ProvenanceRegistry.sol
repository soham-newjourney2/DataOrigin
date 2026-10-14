// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ProvenanceRegistry — append-only registry of artifact hashes (dataset versions, experiments).
contract ProvenanceRegistry {
    struct Record {
        bytes32 hash;
        string artifactType;
        string artifactId;
        address registrant;
        uint256 timestamp;
    }

    mapping(bytes32 => Record) private records;

    event Registered(
        bytes32 indexed hash, string artifactType, string artifactId, address indexed registrant, uint256 timestamp
    );

    function register(bytes32 hash, string calldata artifactType, string calldata artifactId) external {
        require(records[hash].timestamp == 0, "already registered");
        records[hash] = Record(hash, artifactType, artifactId, msg.sender, block.timestamp);
        emit Registered(hash, artifactType, artifactId, msg.sender, block.timestamp);
    }

    function getRecord(bytes32 hash) external view returns (Record memory) {
        return records[hash];
    }

    function isRegistered(bytes32 hash) external view returns (bool) {
        return records[hash].timestamp != 0;
    }
}
