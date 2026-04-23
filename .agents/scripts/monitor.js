const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// 設定
const WATCH_DIR = path.join(process.cwd(), 'src');
const ANALYZE_DIR = path.join(process.cwd(), '.analyze');
const LOCK_FILE = path.join(ANALYZE_DIR, 'analyzer.lock');
const LOG_FILE = path.join(ANALYZE_DIR, 'monitor.log');

// ログ出力関数
function log(message) {
    const timestamp = new Date().toISOString();
    const formattedMessage = `[${timestamp}] ${message}\n`;
    console.log(message);
    if (!fs.existsSync(ANALYZE_DIR)) fs.mkdirSync(ANALYZE_DIR, { recursive: true });
    fs.appendFileSync(LOG_FILE, formattedMessage);
}

// ステータス更新関数
function updateStatus(state, details = {}) {
    const statusPath = path.join(ANALYZE_DIR, 'status.json');
    const statusData = {
        state: state, // 'analyzing', 'success', 'error', 'idle'
        lastUpdate: new Date().toISOString(),
        ...details
    };
    if (!fs.existsSync(ANALYZE_DIR)) fs.mkdirSync(ANALYZE_DIR, { recursive: true });
    fs.writeFileSync(statusPath, JSON.stringify(statusData, null, 2));
}

// 1. 環境セットアップ
function setup() {
    log('🚀 Starting setup...');
    try {
        require.resolve('chokidar');
        log('✅ chokidar is already installed.');
    } catch (e) {
        log('📦 chokidar not found. Installing locally in .analyze...');
        try {
            // プロジェクト内に chokidar をインストール
            execSync('npm install chokidar', { cwd: process.cwd(), stdio: 'inherit' });
            log('✅ chokidar installed successfully.');
        } catch (err) {
            log('❌ Failed to install chokidar. Please check your internet connection or Node.js environment.');
            process.exit(1);
        }
    }
}

// 2. 解析実行ロジック (旧 agent_analyzer.py の移植)
async function runAnalysis(event, filePath) {
    if (fs.existsSync(LOCK_FILE)) {
        log(`⚠️ Skipping trigger (${event} on ${filePath}): Analysis already in progress.`);
        return;
    }

    try {
        fs.writeFileSync(LOCK_FILE, process.pid.toString());
        updateStatus('analyzing', { trigger: { event, file: path.basename(filePath) } });
        // 実行するツールリスト
        const tools = [
            { name: 'Repomix', cmd: 'npx', args: ['-y', 'repomix', '--output', '.analyze/repomix.txt', '--include', 'src/**'] },
            { name: 'Ruff', cmd: 'ruff', args: ['check', 'src'] },
            { name: 'Semgrep', cmd: 'semgrep', args: ['scan', '--config', 'auto', 'src', '--json'] }
        ];

        for (const tool of tools) {
            log(`🛠️ Running ${tool.name}...`);
            await new Promise((resolve) => {
                const proc = spawn(tool.cmd, tool.args, { shell: true, stdio: 'inherit' });
                proc.on('close', (code) => {
                    log(`✅ ${tool.name} finished with code ${code}`);
                    resolve();
                });
            });
        }

        log('✨ Analysis complete.');
        updateStatus('success');
    } catch (err) {
        log(`❌ Analysis failed: ${err.message}`);
        updateStatus('error', { error: err.message });
    } finally {
        if (fs.existsSync(LOCK_FILE)) fs.unlinkSync(LOCK_FILE);
        setTimeout(() => updateStatus('idle'), 5000); // 5秒後に idle に戻す
    }
}

// 3. 監視開始
function startMonitor() {
    const chokidar = require('chokidar');
    log(`👀 Monitoring directory: ${WATCH_DIR}`);

    const watcher = chokidar.watch(WATCH_DIR, {
        ignored: /(^|[\/\\])\../, // 隠しファイルを無視
        persistent: true,
        ignoreInitial: true
    });

    watcher
        .on('add', path => runAnalysis('add', path))
        .on('change', path => runAnalysis('change', path))
        .on('unlink', path => runAnalysis('unlink', path));

    log('✅ Watcher is ready and waiting for changes.');
}

// 実行
setup();
startMonitor();
