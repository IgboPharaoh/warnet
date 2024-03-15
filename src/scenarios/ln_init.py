#!/usr/bin/env python3

from time import sleep

from scenarios.utils import ensure_miner
from warnet.test_framework_bridge import WarnetTestFramework


def cli_help():
    return "Fund LN wallets and open channels"


class LNInit(WarnetTestFramework):
    def set_test_params(self):
        self.num_nodes = None

    def run_test(self):
        self.log.info("Get LN nodes and wallet addresses")
        ln_nodes = []
        recv_addrs = []
        for tank in self.warnet.tanks:
            if tank.lnnode is not None:
                recv_addrs.append(tank.lnnode.getnewaddress())
                ln_nodes.append(tank.index)

        self.log.info("Fund LN wallets")
        miner = ensure_miner(self.nodes[0])
        miner_addr = miner.getnewaddress()
        # 298 block base
        self.generatetoaddress(self.nodes[0], 298, miner_addr)
        # divvy up the goods
        split = miner.getbalance() // len(recv_addrs)
        for addr in recv_addrs:
            miner.sendtoaddress(addr, split)
        # confirm funds in block 299
        self.generatetoaddress(self.nodes[0], 1, miner_addr)

        self.log.info(f"Waiting for funds to be spendable: {split} BTC each for {len(recv_addrs)} LN nodes")

        def funded_lnnodes():
            for tank in self.warnet.tanks:
                if tank.lnnode is None:
                    continue
                if int(tank.lnnode.get_wallet_balance()["confirmed_balance"]) < (split * 100000000):
                    return False
            return True
        self.wait_until(funded_lnnodes, timeout=5*60)

        ln_nodes_uri = ln_nodes.copy()
        while len(ln_nodes_uri) > 0:
            self.log.info(f"Waiting for all LN nodes to have URI, LN nodes remaining: {ln_nodes_uri}")
            for index in ln_nodes_uri:
                lnnode = self.warnet.tanks[index].lnnode
                if lnnode.getURI():
                    ln_nodes_uri.remove(index)
            sleep(5)

        self.log.info("Adding p2p connections to LN nodes")
        for edge in self.warnet.graph.edges(data=True):
            (src, dst, data) = edge
            # Copy the L1 p2p topology (where applicable) to L2
            # so we get a more robust p2p graph for lightning
            if "source-policy" not in data and self.warnet.tanks[src].lnnode and self.warnet.tanks[dst].lnnode:
                self.warnet.tanks[src].lnnode.connect_to_tank(dst)

        # Start confirming channel opens in block 300
        self.log.info("Opening channels, one per block")
        chan_opens = []
        for edge in self.warnet.graph.edges(data=True):
            (src, dst, data) = edge
            if "source-policy" in data:
                src_node = self.warnet.get_ln_node_from_tank(src)
                assert src_node is not None
                assert self.warnet.get_ln_node_from_tank(dst) is not None
                self.log.info(f"opening channel {src}->{dst}")
                chan_pt = src_node.open_channel_to_tank(dst, data["source-policy"])
                # We can guarantee deterministic short channel IDs as long as
                # the change output is greater than the channel funding output,
                # which will then be output 0
                assert chan_pt[64:] == ":0"
                chan_opens.append((edge, chan_pt))
                self.log.info(f"  pending channel point: {chan_pt}")
                self.wait_until(lambda chan_pt=chan_pt: chan_pt[:64] in self.nodes[0].getrawmempool())
                self.generatetoaddress(self.nodes[0], 1, miner_addr)
                assert chan_pt[:64] not in self.nodes[0].getrawmempool()
                self.log.info(f"  confirmed in block {self.nodes[0].getblockcount()}")

        # Ensure all channel opens are sufficiently confirmed
        self.generatetoaddress(self.nodes[0], 10, miner_addr)
        ln_nodes_gossip = ln_nodes.copy()
        while len(ln_nodes_gossip) > 0:
            self.log.info(f"Waiting for graph gossip sync, LN nodes remaining: {ln_nodes_gossip}")
            for index in ln_nodes_gossip:
                lnnode = self.warnet.tanks[index].lnnode
                if len(lnnode.lncli("describegraph")["edges"]) == len(chan_opens):
                    ln_nodes_gossip.remove(index)
            sleep(5)

        self.log.info("Updating channel policies")
        for edge, chan_pt in chan_opens:
            (src, dst, data) = edge
            if "target-policy" in data:
                target_node = self.warnet.get_ln_node_from_tank(dst)
                target_node.update_channel_policy(chan_pt, data["target-policy"])

        self.log.info(f"Warnet LN ready with {len(recv_addrs)} nodes and {len(chan_opens)} channels.")

if __name__ == "__main__":
    LNInit().main()
