#!/bin/bash
cd /Users/yongxianggu/yaoguai
echo "[$(date '+%F %T')] 定时重启开始" >> run.log
./venv/bin/python stop_bot.py >> run.log 2>&1
cp state.json "state_backup_$(date +%m%d_%H%M).json"
echo '{"fails": {}, "last_win": 0}' > state.json
rm -f ALERT STOP_AFTER_LEVEL
./venv/bin/python start_bot.py --resume --lan >> run.log 2>&1
