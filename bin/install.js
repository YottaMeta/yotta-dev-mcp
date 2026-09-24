#!/usr/bin/env node
/**
 * yotta-dev-mcp installer.
 *
 * Safe defaults:
 *   --agent <name>  install to one agent's user-level skill directory
 *   --dir <path>    install to an explicit skill directory
 *   --list          print the supported agent directory map
 *   --global        install to all known user-level directories, requires --yes
 *   --dry-run       print the target without writing
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');

const SKILL_NAME = 'yotta-dev-mcp';
const PKG_ROOT = path.join(__dirname, '..');
const AGENT_DIRS = {
  claude: { label: 'Claude Code', dirs: ['.claude/skills'] },
  cursor: { label: 'Cursor', dirs: ['.cursor/skills', '.agents/skills'] },
  codex: { label: 'Codex', dirs: ['.codex/skills'] },
  gemini: { label: 'Gemini CLI', dirs: ['.gemini/skills', '.agents/skills'] },
  goose: { label: 'Goose', dirs: ['.config/goose/skills', '.agents/skills'] },
  amp: { label: 'Amp', dirs: ['.config/agents/skills', '.agents/skills'] },
  opencode: { label: 'OpenCode', dirs: ['.config/opencode/skills'] },
  windsurf: { label: 'Windsurf', dirs: ['.codeium/windsurf/skills'] },
  workbuddy: { label: 'WorkBuddy', dirs: ['.workbuddy/skills'] },
  kiro: { label: 'Kiro', dirs: ['.kiro/skills'] },
  trae: { label: 'Trae Code CLI', dirs: ['.traecli/skills'] },
  'trae-cn': { label: 'Trae IDE', dirs: ['.trae-cn/skills'] },
  qwen: { label: 'Qwen Code', dirs: ['.qwen/skills'] },
  comate: { label: 'Comate', dirs: ['.comate/skills'] },
  codebuddy: { label: 'CodeBuddy Code', dirs: ['.codebuddy/skills'] },
  kimi: { label: 'Kimi Code CLI', dirs: ['.kimi/skills'] },
  agents: { label: 'AGENTS.md', dirs: ['.agents/skills'] },
};

function codexUserDir() {
  const base = process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
  return path.join(base, 'skills');
}

function opencodeUserDir() {
  const base = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
  return path.join(base, 'opencode', 'skills');
}

function resolveUserDir(rel) {
  if (rel === '.codex/skills') return codexUserDir();
  if (rel === '.config/opencode/skills') return opencodeUserDir();
  return path.join(os.homedir(), rel);
}

function shouldSkip(name) {
  return name === 'package.json' || name === 'bin' || name === 'node_modules' ||
    name === '.git' || name === '__pycache__' || name.indexOf('test_') === 0 ||
    name.endsWith('.pyc') || name.endsWith('.pyo');
}

function copyDir(source, target) {
  fs.mkdirSync(target, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    if (shouldSkip(entry.name)) continue;
    const sourcePath = path.join(source, entry.name);
    const targetPath = path.join(target, entry.name);
    if (entry.isDirectory()) {
      copyDir(sourcePath, targetPath);
    } else if (entry.isFile()) {
      fs.copyFileSync(sourcePath, targetPath);
    }
  }
}

function installTo(directory, dryRun) {
  const target = path.join(directory, SKILL_NAME);
  if (dryRun) {
    console.log('[dry-run] install -> ' + target);
    return;
  }
  copyDir(PKG_ROOT, target);
  console.log('installed -> ' + target);
}

function displayDir(rel) {
  if (process.platform === 'win32') return '%USERPROFILE%\\' + rel.replace(/\//g, '\\');
  return '~/' + rel;
}

function main() {
  const args = process.argv.slice(2);
  const dryRun = args.indexOf('--dry-run') !== -1;
  const yes = args.indexOf('--yes') !== -1 || args.indexOf('-y') !== -1;
  const list = args.indexOf('--list') !== -1 || args.indexOf('-l') !== -1;
  const global = args.indexOf('-g') !== -1 || args.indexOf('--global') !== -1;
  const dirIndex = args.indexOf('--dir');
  const agentIndex = args.indexOf('--agent');
  const explicitDir = dirIndex !== -1 ? args[dirIndex + 1] : null;
  const agent = agentIndex !== -1 ? String(args[agentIndex + 1]).toLowerCase() : null;

  if (list) {
    for (const [key, value] of Object.entries(AGENT_DIRS)) {
      console.log('  ' + key.padEnd(10) + value.label.padEnd(18) +
        value.dirs.map(displayDir).join(', '));
    }
    return;
  }
  if (explicitDir) {
    installTo(explicitDir, dryRun);
    return;
  }
  if (agent) {
    const info = AGENT_DIRS[agent];
    if (!info) {
      console.error('Unknown agent: ' + agent + '. Use --dir <path>.');
      process.exitCode = 2;
      return;
    }
    installTo(resolveUserDir(info.dirs[0]), dryRun);
    return;
  }
  if (global) {
    if (!yes) {
      console.error('Refusing --global without --yes. Use --dry-run to preview.');
      process.exitCode = 2;
      return;
    }
    const seen = new Set();
    for (const value of Object.values(AGENT_DIRS)) {
      for (const rel of value.dirs) {
        if (seen.has(rel)) continue;
        seen.add(rel);
        installTo(resolveUserDir(rel), dryRun);
      }
    }
    return;
  }
  console.error('Choose --agent <name>, --dir <path>, or --list. Use --global --yes explicitly.');
  process.exitCode = 2;
}

main();
