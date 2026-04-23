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
    console.log('VSCODE_OBA_START'); // 純粋なテキストのみ出力（VS Code検知用）
    log('🚀 Starting setup...');
    
    // 起動時に古いロックファイルを削除（強制終了対策）
    if (fs.existsSync(LOCK_FILE)) {
        log('🧹 Removing stale lock file from previous run.');
        fs.unlinkSync(LOCK_FILE);
    }

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
        console.log('VSCODE_OBA_START'); // 監視区間開始マーカー
        fs.writeFileSync(LOCK_FILE, process.pid.toString());
        updateStatus('analyzing', { trigger: { event, file: path.basename(filePath) } });
        // 実行するツールリスト
        const tools = [
            { name: 'Repomix', cmd: 'npx', args: ['-y', 'repomix', 'src', '--output', '.analyze/repomix.txt'] },
            { name: 'Ruff', cmd: 'ruff', args: ['check', 'src', '--output-format', 'concise', '--color', 'never'] },
            { name: 'Semgrep', cmd: 'semgrep', args: ['scan', '--config', 'auto', 'src', '--json', '--quiet'] }
        ];

        for (const tool of tools) {
            log(`🛠️ Running ${tool.name}...`);
            await new Promise((resolve) => {
                const stdioConfig = tool.name === 'Semgrep' ? ['inherit', 'pipe', 'pipe'] : ['inherit', 'pipe', 'inherit'];
                const proc = spawn(tool.cmd, tool.args, { 
                    shell: true, 
                    stdio: stdioConfig,
                    env: { ...process.env, PATH: `/opt/homebrew/bin:${process.env.PATH}` }
                });
                
                if (tool.name === 'Semgrep') {
                    proc.stderr.on('data', () => {}); // Discard stderr to keep terminal clean
                }
                
                if (tool.name === 'Ruff') {
                    proc.stdout.on('data', (data) => {
                        const lines = data.toString().split('\n');
                        for (const line of lines) {
                            if (!line.trim()) continue;
                            const match = line.match(/^([^:]+:\d+:\d+:)\s+([A-Z])/);
                            if (match) {
                                const prefix = match[1];
                                const ruleType = match[2];
                                const severity = (ruleType === 'F' || ruleType === 'E') ? 'error' : 'warning';
                                console.log(`${prefix} ${severity}: [Ruff] ${line.slice(prefix.length).trim()}`);
                            } else {
                                console.log(line);
                            }
                        }
                    });
                    proc.on('close', (code) => {
                        log(`✅ ${tool.name} finished with code ${code}`);
                        resolve();
                    });
                } else if (tool.name === 'Semgrep') {
                    let jsonBuffer = '';
                    proc.stdout.on('data', (data) => {
                        jsonBuffer += data.toString();
                    });
                    proc.on('close', (code) => {
                        try {
                            if (jsonBuffer.trim()) {
                                const result = JSON.parse(jsonBuffer);
                                if (result.results && result.results.length > 0) {
                                    for (const issue of result.results) {
                                        const file = issue.path;
                                        const lineNum = issue.start.line;
                                        const col = issue.start.col;
                                        const severity = (issue.extra.severity === 'ERROR') ? 'error' : 'warning';
                                        const msg = issue.extra.message.replace(/\n/g, ' ');
                                        console.log(`${file}:${lineNum}:${col}: ${severity}: [Semgrep] ${msg}`);
                                    }
                                }
                            }
                        } catch (e) {
                            log(`❌ Semgrep parsing failed: ${e.message}`);
                        }
                        log(`✅ ${tool.name} finished with code ${code}`);
                        resolve();
                    });
                } else {
                    proc.stdout.on('data', (data) => process.stdout.write(data));
                    proc.on('close', (code) => {
                        log(`✅ ${tool.name} finished with code ${code}`);
                        resolve();
                    });
                }
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
        console.log('VSCODE_OBA_READY'); // 監視区間終了マーカー
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

    log('Watcher setup complete.');
    // 起動時に初回解析を実行し、既存の問題を問題パネルに表示させる
    runAnalysis('startup', 'workspace');
}

// 実行
setup();
startMonitor();
