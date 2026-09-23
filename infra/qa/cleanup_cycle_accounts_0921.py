"""Exact verified cycle-QA accounts; reuse backed-up transactional cleanup."""
import asyncio, sys
import cleanup_acceptance_users_0919 as cleanup
cleanup.EXPECTED = {51: ('qagoalb591262640', '目标周期验收'), 52: ('qa0920be961b6075', '周期验收0920A'), 53: ('qa09208d56e2f724', '周期验收0920B'), 54: ('qa092028197fac08', '周期验收0920C'), 55: ('qa0920db8e705768', '周期验收0920D'), 56: ('qa092002e75b2c25', '周期验收0920E'), 57: ('qa0920d26c274a1d', '周期验收0920F'), 58: ('qa09201ba4f21c92', '周期验收0920G'), 59: ('qa0920d24d74d1d1', '周期验收0920Deploy0920'), 60: ('qa092059a1dc29c9', '周期验收0920DeployP05'), 61: ('qa0920242da016a5', '周期验收0920DeployP02'), 62: ('qa09202c0dbf852c', '周期验收0920DeployP04'), 63: ('qa0920cb6837e106', '周期验收0920DeployP01'), 64: ('qa092014d31c7023', '周期验收0920DeployP03'), 65: ('qa092041e42a2fd8', '周期验收0920DeployP06'), 66: ('qa0920bf9b67934d', '周期验收0920DeployP07'), 67: ('qa092098b98dc5b6', '周期验收0920DeployP08'), 68: ('qa09206fac968de2', '周期验收0920DeployP09'), 69: ('qa09208b297e3f70', '周期验收0920DeployP10'), 70: ('qa0920bb9bd8c1de', '周期验收0920PushDelay0921'), 71: ('qa092025b69df668', '周期验收0920PushDelay0921-d6a7fbc2')}
if __name__ == "__main__":
    asyncio.run(cleanup.main("--apply" in sys.argv))
