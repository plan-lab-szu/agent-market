// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract SettlementHook {
    mapping(uint256 => bytes32) public settlementProofs;

    event SettlementRecorded(uint256 indexed sessionId, bytes32 proofHash);

    function recordSettlement(uint256 sessionId, bytes32 proofHash) external {
        settlementProofs[sessionId] = proofHash;
        emit SettlementRecorded(sessionId, proofHash);
    }
}
