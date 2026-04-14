# PAOS Runtime Guide 

This guide only focuses on how to run the demo pipeline.

## 1) Install Isaac Sim 5.1 first

- Official download page (5.1.0):  
  [https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/download.html](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/download.html)
- Quick install doc (5.1.0):  
  [https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/quick-install.html](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/quick-install.html)

## 2) Prepare Python environment

```bash
conda activate paos
```

## 3) Install required dependencies

Install both local projects into the same `paos` environment:

```bash
cd /home/zyserver/work/PhyAgentOS
pip install -e .

```

## 4) Start HAL watchdog (GUI mode)

```bash
cd /home/zyserver/work/PhyAgentOS
conda activate paos
python hal/hal_watchdog.py --gui --interval 0.05 --driver pipergo2_manipulation --driver-config examples/pipergo2_manipulation_driver.json
```

## 5) Send PAOS agent commands

Open another terminal:

```bash
cd /home/zyserver/work/PhyAgentOS
conda activate paos
```

Then run commands in order:

```bash
paos agent -m "open simulation"
paos agent -m "go to desk"
paos agent -m "pick up the red cube and move next to the rear pedestal"
```

## 6) Notes

- Keep only one watchdog process running.
- If you modify driver or skill files, restart watchdog.
- If the simulator is laggy, make sure `--interval 0.05` is used.
