// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IERC20Minimal.sol";

contract OrderAnchoredEscrow {
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

    struct Order {
        address ua;
        address sa;
        uint256 amount;
        bytes32 requestHash;
        bytes32 quoteId;
        uint256 expiry;
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
        bool settled;
    }

    IERC20Minimal public immutable token;
    uint256 public nextOrderId;
    mapping(uint256 => Order) public orders;

    event OrderLocked(
        uint256 indexed orderId,
        address indexed ua,
        address indexed sa,
        uint256 amount,
        bytes32 requestHash,
        bytes32 quoteId,
        uint256 expiry
    );
    event OrderSettled(uint256 indexed orderId, address indexed sa, bytes32 proofHash);

    constructor(address tokenAddress) {
        token = IERC20Minimal(tokenAddress);
    }

    function createOrderAndLock(
        address sa,
        uint256 amount,
        OrderInput calldata input,
        bytes32 quoteId,
        uint256 expiry
    ) external returns (uint256 orderId) {
        require(amount > 0, "amount");
        orderId = nextOrderId++;
        _storeOrder(orderId, msg.sender, sa, amount, input, quoteId, expiry);
        require(token.transferFrom(msg.sender, address(this), amount), "transfer");
        emit OrderLocked(orderId, msg.sender, sa, amount, input.requestHash, quoteId, expiry);
    }

    function _storeOrder(
        uint256 orderId,
        address ua,
        address sa,
        uint256 amount,
        OrderInput calldata input,
        bytes32 quoteId,
        uint256 expiry
    ) internal {
        Order storage order = orders[orderId];
        _storeOrderCore(order, ua, sa, amount, input, quoteId, expiry);
        _storeOrderText(order, input);
        _storeOrderMeta(order, input);
        order.settled = false;
    }

    function _storeOrderCore(
        Order storage order,
        address ua,
        address sa,
        uint256 amount,
        OrderInput calldata input,
        bytes32 quoteId,
        uint256 expiry
    ) internal {
        order.ua = ua;
        order.sa = sa;
        order.amount = amount;
        order.requestHash = input.requestHash;
        order.quoteId = quoteId;
        order.expiry = expiry;
        order.taskId = input.taskId;
        order.modelId = input.modelId;
        order.deadline = input.deadline;
    }

    function _storeOrderText(Order storage order, OrderInput calldata input) internal {
        order.prompt = input.prompt;
        order.negativePrompt = input.negativePrompt;
    }

    function _storeOrderMeta(Order storage order, OrderInput calldata input) internal {
        order.width = input.width;
        order.height = input.height;
        order.steps = input.steps;
        order.seed = input.seed;
        order.guidanceScale = input.guidanceScale;
        order.sampler = input.sampler;
        order.metadataUri = input.metadataUri;
    }

    function createOrderAndLockWithAuthorization(
        address payer,
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
    ) external returns (uint256 orderId) {
        require(amount > 0, "amount");
        orderId = nextOrderId++;
        _storeOrder(orderId, payer, sa, amount, input, quoteId, expiry);
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
        emit OrderLocked(orderId, payer, sa, amount, input.requestHash, quoteId, expiry);
    }

    function settleOrder(uint256 orderId, bytes32 proofHash, bytes calldata signature) external {
        Order storage order = orders[orderId];
        require(order.ua != address(0), "missing");
        require(!order.settled, "settled");
        bytes32 expectedProof = keccak256(
            abi.encodePacked(order.requestHash, order.quoteId)
        );
        require(proofHash == expectedProof, "proof");
        bytes32 messageHash = keccak256(abi.encodePacked(orderId, proofHash));
        address signer = _recoverSigner(messageHash, signature);
        require(signer == order.sa, "signature");
        order.settled = true;
        require(token.transfer(order.sa, order.amount), "transfer");
        emit OrderSettled(orderId, order.sa, proofHash);
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
