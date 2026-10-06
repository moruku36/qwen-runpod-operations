# Oct 4 credit bounded execution candidate

This local-only candidate is a fresh source revision for review and CPU testing. It does not create or resume a Pod, and its estimates are not a provider-enforced spend limit.

Every execution packet must carry the actual Pod ID and `RUNNING` status, whether the allocation was a create or resume, timezone-aware actual allocation time, today's JST approval date, an explicit fresh run ID, observed balance and timestamp within 15 minutes, current cost evidence, a maximum runtime, an absolute same-day Stop deadline, a Stop confirmation buffer of at least 10 minutes, a present Stop operator, a verified ordinary console Stop path, and Auto-Pay-disabled confirmation. Work/report targets stay no later than T+80/T+85, with at least 30/20 minutes before the Stop request and at least 60 minutes for work.

The estimate reserves at least $0.528 for 24 hours of the already retained 80GB volume. Additional retained-volume charges are explicit and may be zero only after the resource configuration confirms none is added. The quoted runtime must cover observed GPU plus running-storage rates; a positive delayed-billing margin is also required. No top-up is allowed, but provider billing may differ from estimates.

The packet builder does not authorize create/resume. A separate action-time approval is required. A human must use the verified RunPod console Stop control and confirm Compute is no longer running. Local deadline cancellation does not stop billing. Terminate/data deletion remain separate actions.
