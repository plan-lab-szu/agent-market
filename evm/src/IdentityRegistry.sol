// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract IdentityRegistry {
    mapping(bytes32 => address) public didOwner;

    event IdentityRegistered(bytes32 indexed didHash, address indexed owner);

    function register(bytes32 didHash) external {
        require(didOwner[didHash] == address(0), "exists");
        didOwner[didHash] = msg.sender;
        emit IdentityRegistered(didHash, msg.sender);
    }
}
