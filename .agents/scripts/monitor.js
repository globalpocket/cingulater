const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const WATCH_DIR = path.join(process.cwd(), 'src');
const ANALYZE_DIR = path.join(process.cwd(), '.analyze');
let isAnalyzing = false;

function setup() {
    if (!fs.existsSync(ANALYZE_DIR)) {
        fs.mkdirSync(ANALYZE_DIR, { recursive: true });
    }

    try {
        require.resolve('chokidar');
    } catch (e) {
        try {
            execSync('npm install chokidar', { cwd: process.cwd(), stdio: 'ignore' });
        } catch (err) {
            console.error('[Analyzer] Failed to install chokidar.');
            process.exit(1);
        }
    }
}

async function runAnalysis(event, filePath) {
    if (isAnalyzing) return;
    isAnalyzing = true;

    try {
        console.log('[Analyzer] Analysis started');

        const tools = [
            { name: 'Repomix', cmd: 'npx', args: ['-y', 'repomix', 'src', '--output', '.analyze/repomix.txt'] },
            { name: 'Ruff', cmd: 'ruff', args: ['check', 'src', '--output-format', 'sarif', '--output-file', '.analyze/ruff.sarif'] },
            { name: 'Semgrep', cmd: 'semgrep', args: ['scan', '--config', 'auto', 'src', '--sarif', '--output', '.analyze/semgrep.sarif', '--quiet'] }
        ];

        for (const tool of tools) {
            await new Promise((resolve) => {
                const proc = spawn(tool.cmd, tool.args, {
                    shell: true,
                    stdio: 'ignore', // SARIFファイル等へ出力されるため標準出力・エラーは無視
                    env: { ...process.env, PATH: `/opt/homebrew/bin:${process.env.PATH}` }
                });
                proc.on('close', resolve);
            });
        }

    } catch (err) {
        console.error(`[Analyzer] Error: ${err.message}`);
    } finally {
        console.log('[Analyzer] Analysis finished');
        setTimeout(() => { isAnalyzing = false; }, 2000); // 連続発火を防ぐデバウンス
    }
}

function startMonitor() {
    const chokidar = require('chokidar');

    const watcher = chokidar.watch(WATCH_DIR, {
        ignored: /(^|[\/\\])\../,
        persistent: true,
        ignoreInitial: true
    });

    watcher
        .on('add', path => runAnalysis('add', path))
        .on('change', path => runAnalysis('change', path))
        .on('unlink', path => runAnalysis('unlink', path));

    // 初回実行
    runAnalysis('startup', 'workspace');
}

setup();
startMonitor();
