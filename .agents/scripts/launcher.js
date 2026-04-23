const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const ANALYZE_DIR = path.join(process.cwd(), '.analyze');
const PIDS_FILE = path.join(ANALYZE_DIR, '.vscode_pids.json');
const LOCK_FILE = path.join(ANALYZE_DIR, 'analyzer.lock');
const MONITOR_SCRIPT = path.join(process.cwd(), '.agents', 'scripts', 'monitor.js');

function ensureDirExists() {
    if (!fs.existsSync(ANALYZE_DIR)) {
        fs.mkdirSync(ANALYZE_DIR, { recursive: true });
    }
}

function updateVscodePids() {
    const currentPid = process.env.VSCODE_PID;
    if (!currentPid) return;

    let pids = [];
    if (fs.existsSync(PIDS_FILE)) {
        try {
            pids = JSON.parse(fs.readFileSync(PIDS_FILE, 'utf8'));
        } catch (e) {
            pids = [];
        }
    }

    if (!pids.includes(currentPid)) {
        pids.push(currentPid);
        fs.writeFileSync(PIDS_FILE, JSON.stringify(pids, null, 2));
    }
}

function isMonitorRunning() {
    if (!fs.existsSync(LOCK_FILE)) return false;
    
    try {
        const pidStr = fs.readFileSync(LOCK_FILE, 'utf8').trim();
        const pid = parseInt(pidStr, 10);
        if (isNaN(pid)) return false;
        
        // 0を送信して死活チェック
        process.kill(pid, 0);
        return true;
    } catch (e) {
        // プロセスが存在しない場合は例外が発生する
        return false;
    }
}

function startMonitor() {
    if (isMonitorRunning()) return;

    // monitor.js を detached モードでバックグラウンド起動
    const child = spawn('node', [MONITOR_SCRIPT], {
        detached: true,
        stdio: 'ignore', // 標準入出力を完全に切り離す
        cwd: process.cwd(),
        env: process.env
    });

    // ロックファイルにプロセスのPIDを記録
    if (child.pid) {
        fs.writeFileSync(LOCK_FILE, child.pid.toString());
    }

    // 親プロセスから切り離す
    child.unref();
}

function main() {
    ensureDirExists();
    updateVscodePids();
    startMonitor();
    
    // ランチャータスクを即座に終了させる
    process.exit(0);
}

main();
