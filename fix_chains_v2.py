import yaml, glob, os
ROUTER='http://localhost:7022/v1'
SONNET={'designer','director','graphic','social'}

def R(model):
    return {'provider':'custom','model':model,'base_url':ROUTER,'api_key':'not-required'}

# Tiered so that every hop is a DIFFERENT billing account / failure domain:
#   1 kimi k3        Moonshot coding sub  (self-heals at cycle refresh)
#   2 deepseek-v4    NVIDIA Build key     (independent credit pool, verified live)
#   3 claude native  Anthropic sub        (best quality + prompt caching)
#   4 claude router  Anthropic via :7022  (uses ~/.claude/.credentials.json, the
#                                          healthy cred — works even while the
#                                          Hermes pool prefers a depleted one)
#   5 nemotron-omni  local Ollama         (free, cannot rate limit)
for path in sorted(glob.glob('/home/codex450/.hermes/profiles/*/config.yaml')):
    name=os.path.basename(os.path.dirname(path))
    cfg=yaml.safe_load(open(path)) or {}
    claude='claude-sonnet-5' if name in SONNET else 'claude-opus-5'
    cfg['fallback_providers']=[
        R('deepseek-v4'),
        {'provider':'anthropic','model':claude},
        R('claude-sonnet'),
        R('nemotron-omni'),
    ]
    yaml.safe_dump(cfg, open(path,'w'), sort_keys=False, default_flow_style=False, width=100)
    print(f'{name:12s} k3 -> deepseek-v4 -> {claude} -> router:claude-sonnet -> nemotron-omni')

# Root: default is Claude (Blake's explicit pick), so its chain leads with the
# non-Anthropic tiers to avoid two consecutive Anthropic attempts.
P='/home/codex450/.hermes/config.yaml'
cfg=yaml.safe_load(open(P))
cfg['fallback_providers']=[R('claude-sonnet'), {'provider':'kimi-coding','model':'k3'},
                           R('deepseek-v4'), R('nemotron-omni')]
yaml.safe_dump(cfg, open(P,'w'), sort_keys=False, default_flow_style=False, width=100)
print('\nroot: claude-sonnet-5(native) -> router:claude-sonnet -> k3 -> deepseek-v4 -> nemotron-omni')
