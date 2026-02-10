// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../src/IdentityRegistry.sol";
import "../src/OnchainOrderBook.sol";
import "../src/MockUSDC.sol";
import "../src/ServiceEscrow.sol";
import "../src/SettlementHook.sol";

interface Vm {
    function addr(uint256 privateKey) external returns (address);

    function sign(uint256 privateKey, bytes32 digest)
        external
        returns (uint8 v, bytes32 r, bytes32 s);

    function deal(address who, uint256 amount) external;

    function prank(address who) external;

    function startPrank(address who) external;

    function stopPrank() external;
}

contract CostContractsTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    struct AuthData {
        uint256 validAfter;
        uint256 validBefore;
        uint256 expiry;
        bytes32 quoteId;
        bytes32 nonce;
        uint8 v;
        bytes32 r;
        bytes32 s;
    }

    function signAuthorization(
        MockUSDC token,
        uint256 ownerKey,
        address owner,
        address spender,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce
    ) internal returns (uint8 v, bytes32 r, bytes32 s) {
        bytes32 structHash = keccak256(
            abi.encode(
                token.TRANSFER_WITH_AUTHORIZATION_TYPEHASH(),
                owner,
                spender,
                value,
                validAfter,
                validBefore,
                nonce
            )
        );
        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", token.DOMAIN_SEPARATOR(), structHash)
        );
        return vm.sign(ownerKey, digest);
    }

    function buildAuth(
        MockUSDC token,
        uint256 ownerKey,
        address owner,
        address spender,
        uint256 amount,
        bytes32 quoteId,
        bytes32 nonce
    ) internal returns (AuthData memory) {
        AuthData memory auth;
        auth.validAfter = 0;
        auth.validBefore = block.timestamp + 1 days;
        auth.expiry = block.timestamp + 1 days;
        auth.quoteId = quoteId;
        auth.nonce = nonce;
        (auth.v, auth.r, auth.s) = signAuthorization(
            token,
            ownerKey,
            owner,
            spender,
            amount,
            auth.validAfter,
            auth.validBefore,
            nonce
        );
        return auth;
    }

    function buildOrderBookInput(address token)
        internal
        pure
        returns (OnchainOrderBook.OrderInput memory)
    {
        bytes32[8] memory prompt;
        bytes32[4] memory negativePrompt;
        bytes32[2] memory metadataUri;
        return OnchainOrderBook.OrderInput({
            taskId: keccak256("task"),
            sa: address(0xBEEF),
            token: token,
            amount: 1_000_000,
            modelId: keccak256("model"),
            prompt: prompt,
            negativePrompt: negativePrompt,
            width: 512,
            height: 512,
            steps: 30,
            seed: 42,
            guidanceScale: 750,
            sampler: 1,
            deadline: 1_700_000_000,
            metadataUri: metadataUri
        });
    }

    function buildServiceOrderInput()
        internal
        pure
        returns (ServiceEscrow.OrderInput memory)
    {
        bytes32[8] memory prompt;
        bytes32[4] memory negativePrompt;
        bytes32[2] memory metadataUri;
        return ServiceEscrow.OrderInput({
            taskId: keccak256("task"),
            modelId: keccak256("model"),
            requestHash: keccak256("request"),
            prompt: prompt,
            negativePrompt: negativePrompt,
            width: 512,
            height: 512,
            steps: 30,
            seed: 42,
            guidanceScale: 750,
            sampler: 1,
            deadline: 1_700_000_000,
            metadataUri: metadataUri
        });
    }

    function signProof(uint256 signerKey, uint256 sessionId, bytes32 proofHash)
        internal
        returns (bytes memory)
    {
        bytes32 messageHash = keccak256(abi.encodePacked(sessionId, proofHash));
        bytes32 ethSigned = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", messageHash)
        );
        (uint8 vSig, bytes32 rSig, bytes32 sSig) = vm.sign(signerKey, ethSigned);
        return abi.encodePacked(rSig, sSig, vSig);
    }

    function testIdentityRegister() public {
        IdentityRegistry registry = new IdentityRegistry();
        bytes32 did = keccak256(abi.encodePacked("did:example:001"));
        registry.register(did);
        require(registry.didOwner(did) == address(this), "owner mismatch");
    }

    function testOrderBookCreate() public {
        OnchainOrderBook book = new OnchainOrderBook();
        MockUSDC token = new MockUSDC();
        OnchainOrderBook.OrderInput memory orderInput = buildOrderBookInput(
            address(token)
        );
        book.createOrder(orderInput);

        require(book.nextOrderId() == 1, "order id");
    }

    function testEscrowLifecycle() public {
        MockUSDC token = new MockUSDC();
        ServiceEscrow escrow = new ServiceEscrow(address(token));

        uint256 uaKey = 0xA11CE;
        uint256 saKey = 0xB0B;
        address ua = vm.addr(uaKey);
        address sa = vm.addr(saKey);

        vm.deal(ua, 1 ether);
        vm.deal(sa, 1 ether);

        uint256 amount = 1_000_000;
        token.mint(ua, amount);

        AuthData memory auth = buildAuth(
            token,
            uaKey,
            ua,
            address(escrow),
            amount,
            keccak256("quote-1"),
            keccak256("auth-1")
        );
        vm.prank(ua);
        uint256 sessionId = escrow.depositLockWithAuthorization(
            ua,
            sa,
            keccak256("request"),
            amount,
            auth.quoteId,
            auth.expiry,
            auth.validAfter,
            auth.validBefore,
            auth.nonce,
            auth.v,
            auth.r,
            auth.s
        );

        bytes memory signature = signProof(saKey, sessionId, keccak256("proof"));

        vm.prank(sa);
        escrow.submitProofAndRelease(sessionId, keccak256("proof"), signature);

        require(token.balanceOf(sa) == amount, "settlement failed");
        (, , , , , , bool settled) = escrow.sessions(sessionId);
        require(settled, "not settled");
    }

    function testOrderAnchoredFlow() public {
        MockUSDC token = new MockUSDC();
        ServiceEscrow escrow = new ServiceEscrow(address(token));
        SettlementHook hook = new SettlementHook();

        uint256 uaKey = 0xA11CE;
        uint256 saKey = 0xB0B;
        address ua = vm.addr(uaKey);
        address sa = vm.addr(saKey);

        vm.deal(ua, 1 ether);
        vm.deal(sa, 1 ether);

        uint256 amount = 1_000_000;
        token.mint(ua, amount);

        ServiceEscrow.OrderInput memory input = buildServiceOrderInput();
        AuthData memory auth = buildAuth(
            token,
            uaKey,
            ua,
            address(escrow),
            amount,
            keccak256("quote-2"),
            keccak256("auth-2")
        );
        vm.prank(ua);
        uint256 sessionId = escrow.depositOrderWithAuthorization(
            sa,
            amount,
            input,
            auth.quoteId,
            auth.expiry,
            auth.validAfter,
            auth.validBefore,
            auth.nonce,
            auth.v,
            auth.r,
            auth.s
        );

        bytes32 proofHash = keccak256("proof");
        bytes memory signature = signProof(saKey, sessionId, proofHash);

        vm.prank(sa);
        escrow.submitProofAndReleaseWithHook(sessionId, proofHash, signature, address(hook));

        require(token.balanceOf(sa) == amount, "order settle failed");
        require(hook.settlementProofs(sessionId) == proofHash, "hook missing");
    }
}
