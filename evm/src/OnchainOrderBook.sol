// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract OnchainOrderBook {
    struct Order {
        bytes32 taskId;
        address ua;
        address sa;
        address token;
        uint256 amount;
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

    struct OrderInput {
        bytes32 taskId;
        address sa;
        address token;
        uint256 amount;
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

    uint256 public nextOrderId;
    mapping(uint256 => Order) public orders;

    event OrderCreated(uint256 indexed orderId, address indexed ua, address indexed sa, bytes32 taskId);

    function createOrder(OrderInput calldata input) external returns (uint256 orderId) {
        orderId = nextOrderId++;
        Order storage order = orders[orderId];
        _storeOrderCoreA(order, input);
        _storeOrderCoreB(order, input);
        _storeOrderText(order, input);
        _storeOrderMeta(order, input);
        emit OrderCreated(orderId, msg.sender, input.sa, input.taskId);
    }

    function _storeOrderCoreA(Order storage order, OrderInput calldata input) internal {
        order.taskId = input.taskId;
        order.ua = msg.sender;
        order.sa = input.sa;
        order.token = input.token;
        order.amount = input.amount;
        order.modelId = input.modelId;
        order.deadline = input.deadline;
    }

    function _storeOrderCoreB(Order storage order, OrderInput calldata input) internal {
        order.width = input.width;
        order.height = input.height;
        order.steps = input.steps;
        order.seed = input.seed;
        order.guidanceScale = input.guidanceScale;
        order.sampler = input.sampler;
    }

    function _storeOrderText(Order storage order, OrderInput calldata input) internal {
        order.prompt = input.prompt;
        order.negativePrompt = input.negativePrompt;
    }

    function _storeOrderMeta(Order storage order, OrderInput calldata input) internal {
        order.metadataUri = input.metadataUri;
    }
}
