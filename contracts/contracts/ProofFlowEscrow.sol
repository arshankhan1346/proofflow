// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title ProofFlowEscrow
 * @notice Programmable settlement escrow: funds release only after verified milestones.
 * @dev AI evaluates evidence off-chain. Deterministic rules gate on-chain settlement.
 */
contract ProofFlowEscrow is ReentrancyGuard, AccessControl {
    using SafeERC20 for IERC20;

    // ── Roles ──────────────────────────────────────────────────────────
    bytes32 public constant VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    bytes32 public constant ARBITRATOR_ROLE = keccak256("ARBITRATOR_ROLE");

    // ── Enums ──────────────────────────────────────────────────────────
    enum MilestoneStatus {
        CREATED,
        FUNDED,
        SUBMITTED,
        VERIFYING,
        VERIFIED,
        REJECTED,
        RELEASED,
        DISPUTED
    }

    enum ProjectStatus {
        CREATED,
        FUNDED,
        ACTIVE,
        COMPLETED,
        DISPUTED,
        CANCELLED
    }

    // ── Structs ────────────────────────────────────────────────────────
    struct Milestone {
        uint256 id;
        string description;
        uint256 amount;
        uint256 releasedAmount;
        MilestoneStatus status;
        bytes32 verificationHash;
        bytes32 evidenceHash;
        uint256 deadline;
    }

    struct Project {
        uint256 id;
        address client;
        address worker;
        address token;
        uint256 totalBudget;
        uint256 totalFunded;
        uint256 totalReleased;
        ProjectStatus status;
        uint256 milestoneCount;
        mapping(uint256 => Milestone) milestones;
    }

    // ── State ─────────────────────────────────────────────────────────
    uint256 public nextProjectId;
    mapping(uint256 => Project) public projects;

    // On-chain reputation: address => completed milestone count
    mapping(address => uint256) public completedMilestones;
    mapping(address => uint256) public totalSettledValue;

    // ── Events ─────────────────────────────────────────────────────────
    event ProjectCreated(
        uint256 indexed projectId,
        address indexed client,
        address indexed worker,
        address token,
        uint256 totalBudget,
        uint256 milestoneCount
    );

    event ProjectFunded(
        uint256 indexed projectId,
        address indexed client,
        uint256 amount
    );

    event MilestoneAdded(
        uint256 indexed projectId,
        uint256 indexed milestoneId,
        string description,
        uint256 amount,
        uint256 deadline
    );

    event EvidenceSubmitted(
        uint256 indexed projectId,
        uint256 indexed milestoneId,
        bytes32 evidenceHash
    );

    event MilestoneVerified(
        uint256 indexed projectId,
        uint256 indexed milestoneId,
        bytes32 verificationHash
    );

    event MilestoneRejected(
        uint256 indexed projectId,
        uint256 indexed milestoneId
    );

    event MilestoneSettled(
        uint256 indexed projectId,
        uint256 indexed milestoneId,
        uint256 amount,
        address indexed worker
    );

    event DisputeRaised(
        uint256 indexed projectId,
        uint256 indexed milestoneId,
        address indexed raisedBy
    );

    event DisputeResolved(
        uint256 indexed projectId,
        uint256 indexed milestoneId,
        uint256 releaseAmount,
        address indexed resolvedBy
    );

    event ProjectCompleted(uint256 indexed projectId);

    // ── Errors ─────────────────────────────────────────────────────────
    error ProjectNotFound();
    error Unauthorized();
    error InvalidState();
    error InvalidMilestone();
    error InsufficientFunds();
    error AlreadyReleased();
    error InvalidAmount();
    error DeadlineExpired();
    error ZeroAddress();
    error ZeroAmount();

    // ── Constructor ────────────────────────────────────────────────────
    constructor() {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        _grantRole(VERIFIER_ROLE, msg.sender);
        _grantRole(ARBITRATOR_ROLE, msg.sender);
    }

    // ── Project Creation ───────────────────────────────────────────────

    /**
     * @notice Client creates a project with milestones.
     * @param worker Address of the worker
     * @param token ERC-20 token address for payment
     * @param descriptions Array of milestone descriptions
     * @param amounts Array of milestone amounts
     * @param deadlines Array of milestone deadlines (unix timestamps)
     */
    function createProject(
        address worker,
        address token,
        string[] calldata descriptions,
        uint256[] calldata amounts,
        uint256[] calldata deadlines
    ) external returns (uint256 projectId) {
        if (worker == address(0)) revert ZeroAddress();
        if (token == address(0)) revert ZeroAddress();
        if (descriptions.length == 0) revert InvalidMilestone();
        if (
            descriptions.length != amounts.length ||
            descriptions.length != deadlines.length
        ) revert InvalidMilestone();

        projectId = nextProjectId++;
        Project storage p = projects[projectId];
        p.id = projectId;
        p.client = msg.sender;
        p.worker = worker;
        p.token = token;
        p.status = ProjectStatus.CREATED;

        uint256 total;
        for (uint256 i = 0; i < descriptions.length; i++) {
            if (amounts[i] == 0) revert ZeroAmount();
            p.milestones[i] = Milestone({
                id: i,
                description: descriptions[i],
                amount: amounts[i],
                releasedAmount: 0,
                status: MilestoneStatus.CREATED,
                verificationHash: bytes32(0),
                evidenceHash: bytes32(0),
                deadline: deadlines[i]
            });
            total += amounts[i];
            emit MilestoneAdded(projectId, i, descriptions[i], amounts[i], deadlines[i]);
        }

        p.totalBudget = total;
        p.milestoneCount = descriptions.length;

        emit ProjectCreated(projectId, msg.sender, worker, token, total, descriptions.length);
    }

    // ── Funding ────────────────────────────────────────────────────────

    /**
     * @notice Client deposits the full budget into escrow.
     */
    function fundProject(uint256 projectId) external nonReentrant {
        Project storage p = projects[projectId];
        if (p.client == address(0)) revert ProjectNotFound();
        if (msg.sender != p.client) revert Unauthorized();
        if (p.status != ProjectStatus.CREATED) revert InvalidState();

        uint256 remaining = p.totalBudget - p.totalFunded;
        if (remaining == 0) revert InvalidAmount();

        IERC20(p.token).safeTransferFrom(msg.sender, address(this), remaining);
        p.totalFunded += remaining;
        p.status = ProjectStatus.ACTIVE;

        // Mark all milestones as FUNDED
        for (uint256 i = 0; i < p.milestoneCount; i++) {
            if (p.milestones[i].status == MilestoneStatus.CREATED) {
                p.milestones[i].status = MilestoneStatus.FUNDED;
            }
        }

        emit ProjectFunded(projectId, msg.sender, remaining);
    }

    // ── Evidence Submission ────────────────────────────────────────────

    /**
     * @notice Worker submits evidence hash for a milestone.
     */
    function submitEvidence(
        uint256 projectId,
        uint256 milestoneId,
        bytes32 evidenceHash
    ) external {
        Project storage p = projects[projectId];
        if (p.client == address(0)) revert ProjectNotFound();
        if (msg.sender != p.worker) revert Unauthorized();
        if (milestoneId >= p.milestoneCount) revert InvalidMilestone();

        Milestone storage m = p.milestones[milestoneId];
        if (m.status != MilestoneStatus.FUNDED && m.status != MilestoneStatus.REJECTED)
            revert InvalidState();
        if (block.timestamp > m.deadline && m.deadline != 0) revert DeadlineExpired();
        if (evidenceHash == bytes32(0)) revert InvalidAmount();

        m.evidenceHash = evidenceHash;
        m.status = MilestoneStatus.SUBMITTED;

        emit EvidenceSubmitted(projectId, milestoneId, evidenceHash);
    }

    // ── Verification (by authorized verifier) ─────────────────────────

    /**
     * @notice Authorized verifier marks a milestone as verified or rejected.
     */
    function verifyMilestone(
        uint256 projectId,
        uint256 milestoneId,
        bool passed,
        bytes32 verificationHash
    ) external onlyRole(VERIFIER_ROLE) {
        Project storage p = projects[projectId];
        if (p.client == address(0)) revert ProjectNotFound();
        if (milestoneId >= p.milestoneCount) revert InvalidMilestone();

        Milestone storage m = p.milestones[milestoneId];
        if (m.status != MilestoneStatus.SUBMITTED && m.status != MilestoneStatus.VERIFYING)
            revert InvalidState();

        m.verificationHash = verificationHash;

        if (passed) {
            m.status = MilestoneStatus.VERIFIED;
            emit MilestoneVerified(projectId, milestoneId, verificationHash);
        } else {
            m.status = MilestoneStatus.REJECTED;
            emit MilestoneRejected(projectId, milestoneId);
        }
    }

    // ── Settlement ─────────────────────────────────────────────────────

    /**
     * @notice Release full milestone payment to worker after verification.
     */
    function settleMilestone(
        uint256 projectId,
        uint256 milestoneId
    ) external nonReentrant onlyRole(VERIFIER_ROLE) {
        Project storage p = projects[projectId];
        if (p.client == address(0)) revert ProjectNotFound();
        if (milestoneId >= p.milestoneCount) revert InvalidMilestone();

        Milestone storage m = p.milestones[milestoneId];
        if (m.status != MilestoneStatus.VERIFIED) revert InvalidState();

        uint256 toRelease = m.amount - m.releasedAmount;
        if (toRelease == 0) revert AlreadyReleased();
        if (p.totalFunded - p.totalReleased < toRelease) revert InsufficientFunds();

        m.releasedAmount += toRelease;
        m.status = MilestoneStatus.RELEASED;
        p.totalReleased += toRelease;

        completedMilestones[p.worker]++;
        totalSettledValue[p.worker] += toRelease;

        IERC20(p.token).safeTransfer(p.worker, toRelease);

        emit MilestoneSettled(projectId, milestoneId, toRelease, p.worker);
        _checkProjectCompletion(p);
    }

    /**
     * @notice Partial release: arbitrator or verifier releases a portion.
     */
    function partialSettle(
        uint256 projectId,
        uint256 milestoneId,
        uint256 releaseAmount
    ) external nonReentrant {
        require(
            hasRole(VERIFIER_ROLE, msg.sender) || hasRole(ARBITRATOR_ROLE, msg.sender),
            "Unauthorized"
        );

        Project storage p = projects[projectId];
        if (p.client == address(0)) revert ProjectNotFound();
        if (milestoneId >= p.milestoneCount) revert InvalidMilestone();

        Milestone storage m = p.milestones[milestoneId];
        if (
            m.status != MilestoneStatus.VERIFIED &&
            m.status != MilestoneStatus.DISPUTED
        ) revert InvalidState();
        if (releaseAmount == 0) revert ZeroAmount();
        if (m.releasedAmount + releaseAmount > m.amount) revert InvalidAmount();
        if (p.totalFunded - p.totalReleased < releaseAmount) revert InsufficientFunds();

        m.releasedAmount += releaseAmount;
        p.totalReleased += releaseAmount;

        completedMilestones[p.worker]++;
        totalSettledValue[p.worker] += releaseAmount;

        if (m.releasedAmount == m.amount) {
            m.status = MilestoneStatus.RELEASED;
        } else {
            m.status = MilestoneStatus.VERIFIED; // Can settle more later
        }

        IERC20(p.token).safeTransfer(p.worker, releaseAmount);

        emit MilestoneSettled(projectId, milestoneId, releaseAmount, p.worker);
        _checkProjectCompletion(p);
    }

    // ── Disputes ───────────────────────────────────────────────────────

    /**
     * @notice Worker raises a dispute on a rejected milestone.
     */
    function raiseDispute(uint256 projectId, uint256 milestoneId) external {
        Project storage p = projects[projectId];
        if (p.client == address(0)) revert ProjectNotFound();
        if (msg.sender != p.worker && msg.sender != p.client) revert Unauthorized();
        if (milestoneId >= p.milestoneCount) revert InvalidMilestone();

        Milestone storage m = p.milestones[milestoneId];
        if (m.status != MilestoneStatus.REJECTED) revert InvalidState();

        m.status = MilestoneStatus.DISPUTED;
        p.status = ProjectStatus.DISPUTED;

        emit DisputeRaised(projectId, milestoneId, msg.sender);
    }

    /**
     * @notice Arbitrator resolves a dispute with a release amount (0 = full reject).
     */
    function resolveDispute(
        uint256 projectId,
        uint256 milestoneId,
        uint256 releaseAmount
    ) external nonReentrant onlyRole(ARBITRATOR_ROLE) {
        Project storage p = projects[projectId];
        if (p.client == address(0)) revert ProjectNotFound();
        if (milestoneId >= p.milestoneCount) revert InvalidMilestone();

        Milestone storage m = p.milestones[milestoneId];
        if (m.status != MilestoneStatus.DISPUTED) revert InvalidState();
        if (releaseAmount > m.amount - m.releasedAmount) revert InvalidAmount();

        if (releaseAmount > 0) {
            if (p.totalFunded - p.totalReleased < releaseAmount) revert InsufficientFunds();
            m.releasedAmount += releaseAmount;
            p.totalReleased += releaseAmount;

            completedMilestones[p.worker]++;
            totalSettledValue[p.worker] += releaseAmount;

            IERC20(p.token).safeTransfer(p.worker, releaseAmount);
            emit MilestoneSettled(projectId, milestoneId, releaseAmount, p.worker);
        }

        m.status = MilestoneStatus.RELEASED;
        p.status = ProjectStatus.ACTIVE;

        emit DisputeResolved(projectId, milestoneId, releaseAmount, msg.sender);
        _checkProjectCompletion(p);
    }

    // ── Refund (client cancels unfunded or fully-rejected project) ─────

    function cancelProject(uint256 projectId) external nonReentrant {
        Project storage p = projects[projectId];
        if (msg.sender != p.client) revert Unauthorized();
        if (p.status != ProjectStatus.CREATED && p.status != ProjectStatus.ACTIVE)
            revert InvalidState();

        uint256 refundable = p.totalFunded - p.totalReleased;
        p.status = ProjectStatus.CANCELLED;

        if (refundable > 0) {
            IERC20(p.token).safeTransfer(p.client, refundable);
        }
    }

    // ── Views ──────────────────────────────────────────────────────────

    function getProject(uint256 projectId)
        external
        view
        returns (
            address client,
            address worker,
            address token,
            uint256 totalBudget,
            uint256 totalFunded,
            uint256 totalReleased,
            ProjectStatus status,
            uint256 milestoneCount
        )
    {
        Project storage p = projects[projectId];
        if (p.client == address(0)) revert ProjectNotFound();
        return (
            p.client,
            p.worker,
            p.token,
            p.totalBudget,
            p.totalFunded,
            p.totalReleased,
            p.status,
            p.milestoneCount
        );
    }

    function getMilestone(uint256 projectId, uint256 milestoneId)
        external
        view
        returns (
            uint256 id,
            string memory description,
            uint256 amount,
            uint256 releasedAmount,
            MilestoneStatus status,
            bytes32 verificationHash,
            bytes32 evidenceHash,
            uint256 deadline
        )
    {
        Project storage p = projects[projectId];
        if (p.client == address(0)) revert ProjectNotFound();
        if (milestoneId >= p.milestoneCount) revert InvalidMilestone();
        Milestone storage m = p.milestones[milestoneId];
        return (
            m.id,
            m.description,
            m.amount,
            m.releasedAmount,
            m.status,
            m.verificationHash,
            m.evidenceHash,
            m.deadline
        );
    }

    function getReputation(address user)
        external
        view
        returns (uint256 milestones, uint256 settledValue)
    {
        return (completedMilestones[user], totalSettledValue[user]);
    }

    // ── Internal ──────────────────────────────────────────────────────

    function _checkProjectCompletion(Project storage p) internal {
        for (uint256 i = 0; i < p.milestoneCount; i++) {
            if (p.milestones[i].status != MilestoneStatus.RELEASED) return;
        }
        p.status = ProjectStatus.COMPLETED;
        emit ProjectCompleted(p.id);
    }
}