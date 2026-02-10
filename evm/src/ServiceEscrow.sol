// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IERC20Minimal.sol";

interface ISettlementHook {
    function recordSettlement(uint256 sessionId, bytes32 proofHash) external;
}

contract ServiceEscrow {
    struct OrderInput {
        bytes32 taskId;
        bytes32 modelId;
        bytes32 requestHash;
        bytes32[8] prompt;
        bytes32[4] negativePrompt;
        uint16 width;
        uint16 height;
        uint16 steps;
        uint32 seed;
        uint16 guidanceScale;
        uint8 sampler;
        uint64 deadline;
        bytes32[2] metadataUri;
    }

    struct OrderData {
        bytes32 taskId;
        bytes32 modelId;
        bytes32[8] prompt;
        bytes32[4] negativePrompt;
        uint16 width;
        uint16 height;
        uint16 steps;
        uint32 seed;
        uint16 guidanceScale;
        uint8 sampler;
        uint64 deadline;
        bytes32[2] metadataUri;
    }

    struct Session {
        address ua;
        address sa;
        uint256 amount;
        bytes32 requestHash;
        bytes32 quoteId;
        uint256 expiry;
        bool settled;
    }

    IERC20Minimal public immutable token;
    uint256 public nextSessionId;
    mapping(uint256 => Session) public sessions;
    mapping(uint256 => OrderData) public orders;

    event SessionLocked(
        uint256 indexed sessionId,
        address indexed ua,
        address indexed sa,
        uint256 amount,
        bytes32 requestHash,
        bytes32 quoteId,
        uint256 expiry
    );
    event SessionSettled(uint256 indexed sessionId, address indexed sa, bytes32 proofHash);

    constructor(address tokenAddress) {
        token = IERC20Minimal(tokenAddress);
    }

    function depositLock(
        address sa,
        bytes32 requestHash,
        uint256 amount,
        bytes32 quoteId,
        uint256 expiry
    ) external returns (uint256 sessionId) {
        require(token.transferFrom(msg.sender, address(this), amount), "transfer");
        sessionId = _createSession(sa, requestHash, amount, quoteId, expiry);
    }

    function depositLockWithAuthorization(
        address payer,
        address sa,
        bytes32 requestHash,
        uint256 amount,
        bytes32 quoteId,
        uint256 expiry,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external returns (uint256 sessionId) {
        token.transferWithAuthorization(
            payer,
            address(this),
            amount,
            validAfter,
            validBefore,
            nonce,
            v,
            r,
            s
        );
        sessionId = _createSession(sa, requestHash, amount, quoteId, expiry);
    }

    function depositOrderWithAuthorization(
        address sa,
        uint256 amount,
        OrderInput calldata input,
        bytes32 quoteId,
        uint256 expiry,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external returns (uint256 sessionId) {
        token.transferWithAuthorization(
            msg.sender,
            address(this),
            amount,
            validAfter,
            validBefore,
            nonce,
            v,
            r,
            s
        );
        sessionId = _createSession(sa, input.requestHash, amount, quoteId, expiry);
        OrderData storage order = orders[sessionId];
        order.taskId = input.taskId;
        order.modelId = input.modelId;
        order.prompt = input.prompt;
        order.negativePrompt = input.negativePrompt;
        order.width = input.width;
        order.height = input.height;
        order.steps = input.steps;
        order.seed = input.seed;
        order.guidanceScale = input.guidanceScale;
        order.sampler = input.sampler;
        order.deadline = input.deadline;
        order.metadataUri = input.metadataUri;
    }

    function settleDirect(uint256 sessionId) external {
        Session storage session = sessions[sessionId];
        require(session.ua != address(0), "missing");
        require(!session.settled, "settled");
        require(msg.sender == session.ua || msg.sender == session.sa, "caller");
        session.settled = true;
        require(token.transfer(session.sa, session.amount), "transfer");
        emit SessionSettled(sessionId, session.sa, bytes32(0));
    }

    function submitProofAndRelease(uint256 sessionId, bytes32 proofHash, bytes calldata signature) external {
        _settle(sessionId, proofHash, signature);
    }

    function submitProofAndReleaseWithHook(
        uint256 sessionId,
        bytes32 proofHash,
        bytes calldata signature,
        address hook
    ) external {
        require(hook != address(0), "hook");
        ISettlementHook(hook).recordSettlement(sessionId, proofHash);
        _settle(sessionId, proofHash, signature);
    }

    function _settle(uint256 sessionId, bytes32 proofHash, bytes memory signature) internal {
        Session storage session = sessions[sessionId];
        require(session.ua != address(0), "missing");
        require(!session.settled, "settled");
        bytes32 messageHash = keccak256(abi.encodePacked(sessionId, proofHash));
        address signer = _recoverSigner(messageHash, signature);
        require(signer == session.sa, "signature");
        session.settled = true;
        require(token.transfer(session.sa, session.amount), "transfer");
        emit SessionSettled(sessionId, session.sa, proofHash);
    }

    function _createSession(
        address sa,
        bytes32 requestHash,
        uint256 amount,
        bytes32 quoteId,
        uint256 expiry
    ) internal returns (uint256 sessionId) {
        require(amount > 0, "amount");
        sessionId = nextSessionId++;
        sessions[sessionId] = Session({
            ua: msg.sender,
            sa: sa,
            amount: amount,
            requestHash: requestHash,
            quoteId: quoteId,
            expiry: expiry,
            settled: false
        });
        emit SessionLocked(sessionId, msg.sender, sa, amount, requestHash, quoteId, expiry);
    }

    function _recoverSigner(bytes32 messageHash, bytes memory signature) internal pure returns (address) {
        require(signature.length == 65, "sig");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly ("memory-safe") {
            r := mload(add(signature, 32))
            s := mload(add(signature, 64))
            v := byte(0, mload(add(signature, 96)))
        }
        if (v < 27) {
            v += 27;
        }
        bytes32 ethSigned = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", messageHash));
        return ecrecover(ethSigned, v, r, s);
    }
}
