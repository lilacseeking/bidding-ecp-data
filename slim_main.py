"""Apply Agent A's recommended changes: slim models, share Stage1, remove dead code.
Changes by priority:
  P2: Slim to 8 models (baselines + GBDT + regularized)
  P3: Share Stage1 classifier across all 2S models
  P4: Unify framework - remove redundant run_two_stage
  P6: Remove dead code (NHiTS, VMD, ModernTCN, DLinear, GP-2S)
"""
import os, sys, io, re; sys.stdout=io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
PATH = r'C:\Users\董文涛\PycharmProjects\vmd-catboost\main.py'
with open(PATH, 'r', encoding='utf-8') as f: c = f.read()

# === 1. Delete all model function definitions that will be removed ===
# Remove: run_dlinear, run_dlinear_2s, run_moderntcn, run_moderntcn_2s,
#         run_lightgbm_pure, DLinear class, ModernTCN classes
#         run_nhits, NHitsBlock, NHitsModel
#         VMD functions, run_ridge_2s
#         run_gp_2s

# Strategy: find and remove each function block
def remove_func_block(code, func_name):
    """Remove a function definition from the code."""
    idx = code.find(f'def {func_name}(')
    if idx < 0: return code, False
    # Find the next def at the same indentation level
    next_def = code.find('\ndef ', idx + 10)
    if next_def < 0: next_def = code.find('\n# ====', idx + 10)
    if next_def < 0: return code, False
    return code[:idx] + code[next_def:], True

# Remove classes
for cls in ['class DLinearModel', 'class ModernTCNBlock', 'class ModernTCN', 'class NHitsBlock', 'class NHitsModel']:
    idx = c.find(cls)
    if idx >= 0:
        # Find next class or def
        next_cls = c.find('\nclass ', idx+10)
        next_def = c.find('\ndef ', idx+10)
        end = min(next_cls if next_cls>0 else 999999, next_def if next_def>0 else 999999)
        if end < 999999:
            c = c[:idx] + c[end:]
            print(f'[REMOVE] {cls.split()[1]}')

# Remove functions
for fn in ['run_dlinear', 'run_dlinear_2s', 'run_moderntcn', 'run_moderntcn_2s',
           'run_lightgbm_pure', 'run_nhits', 'run_ridge_2s', 'run_gp_2s',
           'run_vmd_catboost', 'run_vmd_transformer_catboost', 'run_vmd_transformer_direct_sum',
           'run_vmd_svr', 'vmd_decompose_full', 'extrapolate_imfs', 'vmd_optimize_k',
           'filter_imfs_by_correlation']:
    c, removed = remove_func_block(c, fn)
    if removed: print(f'[REMOVE] {fn}')

# === 2. Remove model calls from main() ===
# These model calls to remove from the main loop:
models_to_remove_calls = [
    'NaiveMean', 'Persistence', 'TSB', 'Theta', 'SES',
    'Croston-SBA', 'DLinear', 'DLinear-2S', 'ModernTCN', 'ModernTCN-2S',
    'LightGBM-pure', 'GP-2S', 'CondCatBoost', 'CatBoost-2S', 'NHiTS',
    'LightGBM', 'Ridge-2S', 'ElasticNet-2S',  # removing old locations
]

# Actually, let me be more strategic. I'll remove all model call blocks
# that I don't want, AND also remove the redundant run_two_stage call.

# Remove specific logger.info + model call blocks
call_blocks = [
    'NaiveMean', 'TSB', 'Persistence', 'Theta', 'SES',
    'Croston-SBA', 'DLinear', 'DLinear-2S', 'ModernTCN', 'ModernTCN-2S',
    'LightGBM-pure', 'GP-2S'
]

for model in call_blocks:
    # Find and comment out these blocks
    pattern = f"# --- {model}"
    if model in ['NaiveMean','TSB','Persistence']:
        pattern = f"# --- Baseline: {model}"
    elif model in ['DLinear','DLinear-2S','ModernTCN','ModernTCN-2S','LightGBM-pure','GP-2S']:
        continue  # handled below

# Better approach: find specific patterns and remove them
# The main loop structure: "# --- ModelName ---\n            y_pred_xx..."
# Remove specific blocks

def delete_between(content, start_pat, end_pat):
    idx = content.find(start_pat)
    if idx < 0: return content
    end = content.find(end_pat, idx + len(start_pat))
    if end < 0: return content
    return content[:idx] + content[end:]

# Remove NaiveMean call block
c = delete_between(c, '# --- Baseline: Naive-Mean ---', '# --- Baseline: TSB ---')
print('[REMOVE] NaiveMean call')
c = delete_between(c, '# --- Baseline: TSB ---', '# --- Baseline: Persistence ---')
print('[REMOVE] TSB call')
# Keep NaiveSeasonal and Persistence right before SARIMA
# Keep SARIMA

# Remove after SARIMA until TwoStage
c = delete_between(c, '# --- Croston-SBA', '# --- Theta ---')
c = delete_between(c, '# --- Theta ---', '# --- SES ---')
c = delete_between(c, '# --- SES ---', '# --- DLinear ---')
c = delete_between(c, '# --- DLinear ---', '# --- DLinear-2S ---')
c = delete_between(c, '# --- DLinear-2S ---', '# --- ModernTCN ---')
c = delete_between(c, '# --- ModernTCN ---', '# --- ModernTCN-2S ---')
c = delete_between(c, '# --- ModernTCN-2S ---', '# --- LightGBM(朴素) ---')
c = delete_between(c, '# --- LightGBM(朴素) ---', '# --- LightGBM 对比 ---')
c = delete_between(c, '# --- LightGBM 对比 ---', '# --- TwoStage-Ridge ---')
c = delete_between(c, '# --- TwoStage-Ridge ---', '# --- LightGBM 对比 ---')
c = delete_between(c, '# --- ElasticNet-2S ---', '# --- GaussianProcess-2S ---')
c = delete_between(c, '# --- GaussianProcess-2S ---', '# --- Theta ---')
print('[REMOVE] stale call blocks')

# === 3. Add shared Stage1 classifier + simplified model calls ===
# Find the TwoStage call block
ts_start = c.find("y_pred_ts, y_test_ts, imp_ts, model_ts = run_two_stage(")
if ts_start < 0:
    print('[WARN] run_two_stage call not found')

# Find the VMD disabled section to insert new code before it
vmd_section = c.find('# --- VMD-CatBoost / VMD-Transformer-CatBoost / VMD-SVR ---')
if vmd_section > 0:
    # Find the line start
    line_start = c.rfind('\n', 0, vmd_section) + 1
    new_model_calls = '''
            # --- 共享 Stage1 分类器 (所有2S模型共用) ---
            prob_2s = None
            y_bin = (y_train > 0).astype(int)
            if y_bin.sum() >= 5 and (len(y_bin) - y_bin.sum()) >= 5:
                cls_shared = CatBoostClassifier(iterations=800, learning_rate=0.03, depth=5, l2_leaf_reg=5,
                    loss_function=\"Logloss\", early_stopping_rounds=30, random_state=RANDOM_SEED, verbose=0)
                nv_cls = max(6, len(y_train)//4)
                cls_shared.fit(X_train_factors[:-nv_cls], y_bin[:-nv_cls], eval_set=(X_train_factors[-nv_cls:], y_bin[-nv_cls:]))
                prob_2s = np.clip(cls_shared.predict_proba(X_test_factors)[:,1], 0, 1)

            # --- ElasticNet-2S ---
            from sklearn.linear_model import ElasticNetCV
            en = ElasticNetCV(l1_ratio=[0.1,0.5,0.7,0.9,0.95,1.0], cv=5, max_iter=5000, random_state=42)
            nz = y_train > 0
            if nz.sum() >= 10 and prob_2s is not None:
                nv2 = max(4, nz.sum()//5)
                en.fit(X_train_factors[nz][:-nv2], y_train[nz][:-nv2])
                y_pred_en = prob_2s * np.maximum(en.predict(X_test_factors), 0)
            else:
                en.fit(X_train_factors, y_train)
                y_pred_en = np.maximum(en.predict(X_test_factors), 0)
            metrics_en = evaluate_model(y_test, y_pred_en)
            all_results[material][\"ElasticNet-2S\"] = {\"y_pred\": y_pred_en, \"y_test\": y_test, \"metrics\": metrics_en}
            all_metrics[material][\"ElasticNet-2S\"] = metrics_en

            # --- CatBoost-2S ---
            cb2 = CatBoostRegressor(iterations=1500, learning_rate=0.02, depth=6, l2_leaf_reg=3,
                loss_function=\"RMSE\", early_stopping_rounds=50, random_seed=RANDOM_SEED, verbose=0)
            if nz.sum() >= 10 and prob_2s is not None:
                nv2 = max(4, nz.sum()//5)
                cb2.fit(X_train_factors[nz][:-nv2], y_train[nz][:-nv2], eval_set=(X_train_factors[nz][-nv2:], y_train[nz][-nv2:]))
                y_pred_cb2 = prob_2s * np.maximum(cb2.predict(X_test_factors), 0)
            else:
                nv = min(12, len(y_train)//4)
                cb2.fit(X_train_factors[:-nv], y_train[:-nv], eval_set=(X_train_factors[-nv:], y_train[-nv:]))
                y_pred_cb2 = np.maximum(cb2.predict(X_test_factors), 0)
            metrics_cb2 = evaluate_model(y_test, y_pred_cb2)
            all_results[material][\"CatBoost-2S\"] = {\"y_pred\": y_pred_cb2, \"y_test\": y_test, \"metrics\": metrics_cb2}
            all_metrics[material][\"CatBoost-2S\"] = metrics_cb2
            logger.info(f\"  CatBoost-2S: R2={metrics_cb2[\"R2\"]:.4f}, ElasticNet-2S: R2={metrics_en[\"R2\"]:.4f}\")
'''
    c = c[:line_start] + new_model_calls + c[line_start:]
    print('[ADD] Shared Stage1 + ElasticNet-2S + CatBoost-2S calls')

# === 4. Remove old model call blocks that are now redundant ===
# Delete the old "进阶模型: Conditional CatBoost" through "TwoStage" block
old_block_start = c.find('# --- 进阶模型: Conditional CatBoost (批次事件驱动) ---')
old_block_end = c.find('return y_pred_orig, y_test_orig, None, (cls, reg)', old_block_start)
if old_block_end > 0:
    old_block_end = c.find('\n', old_block_end) + 1
    c = c[:old_block_start] + c[old_block_end:]
    print('[REMOVE] old conditional/TwoStage blocks')

# === 5. Update MODEL_ORDER ===
c = c.replace(
    "MODEL_ORDER = ['CatBoost', 'NaiveSeasonal', 'NaiveMean', 'Persistence', 'SARIMA', 'TSB', 'Croston-SBA',",
    "MODEL_ORDER = ['CatBoost', 'NaiveSeasonal', 'Persistence', 'SARIMA', 'Croston-SBA',")
c = c.replace("'Theta', 'SES', 'DLinear', 'DLinear-2S', 'ModernTCN', 'ModernTCN-2S',\n                    'CatBoost-2S', 'Ridge-2S', 'ElasticNet-2S', 'GP-2S',\n                    'CondCatBoost', 'NHiTS', 'LightGBM-pure', 'LightGBM', 'TwoStage']",
              "'CatBoost-2S', 'ElasticNet-2S', 'LightGBM', 'TwoStage']")
print('[UPDATE] MODEL_ORDER slimmed to 8+1 models')

# === 6. Remove unused import (SVR, GridSearchCV from sklearn) ===
# Keep them - they don't hurt and removing might break things

# === 7. Remove run_two_stage function definition ===
c, ok = remove_func_block(c, 'run_two_stage')
if ok: print('[REMOVE] run_two_stage function')

# === 8. Remove run_conditional_catboost function definition ===
c, ok = remove_func_block(c, 'run_conditional_catboost')
if ok: print('[REMOVE] run_conditional_catboost function')

# Write back
with open(PATH, 'w', encoding='utf-8') as f: f.write(c)
import py_compile
try:
    py_compile.compile(PATH, doraise=True); print('Syntax OK')
except py_compile.PyCompileError as e: print(f'Syntax ERROR: {e}')
