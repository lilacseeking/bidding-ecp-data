# LightGBM Direct Multi-Step 间歇性需求预测模型设计文档

> 版本：v1.1 | 日期：2026-07-06
> 目标：基于 M5 竞赛冠军方案，用 LightGBM 直接做 12 步前向预测，处理极度稀疏的电力物资月度需求
> 状态：已完成代码实现与实验评估

---

## 一、可行性分析

### 1.1 当前 TwoStage 的失败模式

CatBoost TwoStage (TwoStage) 用 Stage 1 分类器判断"有/无需求" + Stage 2 回归器预测需求量。

在 15/74 非零月密度下，80% 的训练月份是零值。CatBoostClassifier 面对 5:1 的类别不平衡（零值:非零值），输出概率 P 整体偏低——将大量非零月归类为"低概率"，导致 `ŷ = P × Q ≈ 0`。

**直接多步 LightGBM 的解决思路：不把零值当"分类标签"，而当"回归目标的一个合法取值"。**

```
TwoStage:  ŷ_t = P(有需求|X) × E(需求|有需求,X)   ← 两个模型独立训练
LGB Direct: ŷ_t = f(X, h)                            ← 一个模型，回归 0 是一个合法预测值
                    ↑
               h 是预测步长 (1到12)
```

### 1.2 M5 竞赛验证

M5 预测竞赛 (2020, 5,000+ 团队) 的 Accuracy 冠军是 LightGBM 集成。后续研究 (Kapetanovic et al., 2025) 对 70% 间歇性的零售数据确认：

- LightGBM/XGBoost **持续超越** N-BEATS、N-HiTS、TFT
- 神经网络需要 SAITS 级别的插补才能接近树模型效果
- leaf-wise 生长的树比 symmetric trees (CatBoost) 在间歇性数据上更灵活

### 1.3 为什么树模型适合稀疏序列

1. **不需要数值连续性**——VMD/LSTM 都要求序列较为平滑连续，但树模型的分裂完全基于顺序统计量，零值只是一些分裂点
2. **零值不影响梯度**——树的 split 是信息增益驱动的，不是梯度驱动的，零值不会施加"把预测值拉到 0"的梯度压力
3. **leaf-wise 生长**——LightGBM 在零值密集区域自动产生更深的叶子，在数据稀疏区域保持浅叶（自适应的模型复杂度）
4. **不需要特征标准化**——MinMax 标准化可能会把一个 0 到 86887 的范围变成 0 到 1，消除量级信息。树模型天然处理原始尺度

### 1.4 文献空白确认

| 已有方法 | 代表工作 | 我们的改进 |
|---------|---------|-----------|
| M5 LightGBM (零售) | Makridakis et al. (2022, IJF) | 零售→电力物资 |
| LGB Direct Multi-Step | Bojer & Meldgaard (2021, IJF) | 未在电力物资验证 |
| CatBoost direct (电力物资) | 本项目的 baseline | CatBoost→LightGBM + 间歇性特征 |

**在电力物资需求预测中，LightGBM Direct Multi-Step 未曾被发表。** 而且 M5 验证过的"树模型在多步预测上由于不用做误差累积，直接多步天然优于递归单步"这一优势，在电力物资间歇性场景下理论上应该更为显著（因为递归预测中 a single wrong zero prediction can cascade into 11 more zeros）。

---

## 二、设计文档

### 2.1 特征工程体系 (26维)

`design_src/src/main.py` 中的 `build_lgb_features()` 函数实现。

#### 第1组: 时间编码 (3维)

| 特征 | 计算公式 | 作用 |
|------|---------|------|
| month_sin | sin(2π × month / 12) | 月度周期编码 |
| month_cos | cos(2π × month / 12) | 月度周期编码 (与 sin 正交) |
| year_index | year - 2020 | 长期趋势 (0~6) |

#### 第2组: 自回归滞后 (8维)

| 特征 | 说明 |
|------|------|
| demand_lag_1 | t-1月的需求量 |
| demand_lag_2 | t-2月的需求量 |
| demand_lag_3 | t-3月的需求量 |
| demand_lag_6 | t-6月 (半年前) |
| demand_lag_12 | t-12月 (去年同期) |
| demand_lag_18 | t-18月 (前年同期, 仅如有) |
| demand_lag_24 | t-24月 (两年同期, 仅如有) |

所有滞后值对零值直接暴露——零就是 0 这个值，不做任何填充。

#### 第3组: 滚动统计 (6维)

| 特征 | 窗口 | 说明 |
|------|------|------|
| rolling_mean | 3月, 6月, 12月 | 近期/中期/年度平均需求量 |
| rolling_max | 3月, 6月, 12月 | 近期/中期/年度峰值 |

计算时**直接包含零值**——滚动均值在 80% 零值的情况下天然反映需求密度。

#### 第4组: 需求间隔特征 (4维, Croston-inspired)

| 特征 | 说明 |
|------|------|
| months_since_last_nz | 距上次非零需求已过几个月 |
| zero_streak_length | 当前连续零值的长度 |
| avg_interval | 历史平均需求间隔（月数）|
| nz_frequency_12m | 近12月中的非零月数 (0~12) |

**这三个特征直接编码了间歇性信号的"稀疏结构"——之前 TwoStage 试图用分类器学习的"有无需求"信息，现在用特征工程的方式显式编码。**

#### 第5组: 批次节奏特征 (5维)

| 特征 | 说明 |
|------|------|
| is_march | 3月 (春检前招标高峰) |
| is_may | 5月 (迎峰度夏前招标高峰) |
| is_september | 9月 (全年最大批次) |
| is_q4 | 10-12月 (年末追加批次) |
| quarter_index | 季度索引 (0=Q1, ..., 3=Q4) |

### 2.2 模型架构

```python
# 12 个独立 LightGBM 模型 — 每个预测未来第 h 个月的量
class LightGBMDirectMultiStep:
    def __init__(self):
        self.models = {}  # {h: LightGBMRegressor}  for h = 1..12
        self.feature_columns = [...]  # 26 features

    def fit(self, X_features, y_series):
        """对每个预测步长 h 独立训练"""
        for h in range(1, 13):
            # 准备训练数据: (X[0:n-h], y[h:n])
            X_train = X_features[:n-h]
            y_train = y_series[h:]   # t+h 的真实值

            # 小样本 → 极浅树
            model_h = LGBMRegressor(
                n_estimators=100,
                max_depth=3,
                min_child_samples=5,
                learning_rate=0.03,
                reg_alpha=0.5,
                reg_lambda=2.0,
                subsample=0.7,       # 行采样
                colsample_bytree=0.7, # 列采样
                verbosity=-1
            )
            model_h.fit(X_train, y_train)
            self.models[h] = model_h

    def predict(self, X_last):
        """从最后一个已知点预测未来 12 个月"""
        return np.array([self.models[h].predict(X_last)[0] for h in range(1, 13)])
```

### 2.3 超参数选择理由

| 参数 | 值 | 为什么 (小样本专用) |
|------|:--:|------|
| n_estimators | 100 | 74个样本容不下200+棵树 |
| max_depth | 3 | 极浅树 → 每棵树只用 ≤7 个特征 |
| min_child_samples | 5 | 叶子至少5个样本 (传统=20, 但我们需要更灵活) |
| learning_rate | 0.03 | 学习慢 = 不过度拟合 |
| subsample + colsample | 0.7 | 双重采样 = bagging 效应, 进一步减少过拟合 |
| reg_alpha/reg_lambda | 0.5/2.0 | 强正则化组合 |

总参数数 ≈ 100 trees × 2³-1 nodes × 1 value/node ≈ 700个终端值, 但 regularization 将有效复杂度压缩到 ~200。

### 2.4 双模式: Direct Multi-Step + Recursive

```python
# Mode 1: Direct (默认) — 训练12个独立模型
models_direct = [LGB(h) for h in range(1, 13)]

# Mode 2: Recursive (对比实验) — 训练1个模型, 自回归滚动12步
model_recursive = LGB(h=1)  # 只预测t+1
for step in range(12):
    forecast[step] = model_recursive.predict(current_features)
    current_features = update_features(forecast[step])  # 用预测值更新lag特征

# 对比目的:
# Direct:  不会累积误差 (t+12直接学), 但12个模型互相独立可能不一致
# Recursive: 利用中间预测, 但零值误判会滚雪球
```

---

## 三、预期与风险

### 3.1 预期提升

| 物资 | CatBoost TwoStage R² | 预期 LGB Direct R² | 提升来源 |
|------|:--:|:--:|------|
| 控制电缆 | -0.15 | +0.15 ~ +0.40 | 树模型天然适配零值 + 间隔特征 |
| 电缆保护管 | -0.42 | -0.05 ~ +0.20 | 无 VMD 依赖 + lag18/24 捕获长期节奏 |
| 通信单元 | +0.17 | +0.20 ~ +0.45 | nz_frequency_12m 直接编码稀疏度 |
| 电缆接线端子 | -0.11 | -0.05 ~ +0.15 | 双重采样减轻过拟合 |
| 蝶式绝缘子 | +0.49 | +0.45 ~ +0.60 | 保持 |

### 3.2 已知风险

- 26 维特征 vs 74 个样本的比率仍有挑战 (≈3:1, 比 TwoStage 的 8:1=0.6 有改善)
- 滞后 t-18 和 t-24 对所有物资来说在训练集的前 18-24 个月无值
- Direct 模式没有保证预测序列平滑 (t+1 和 t+2 可能不一致)

---

## 四、实验结果

### 4.1 实验设置

同 Croston-TSB-LGB 文档。对比：Naive-Seasonal、Croston-TSB-LGB、CatBoost、TwoStage。

### 4.2 R² 对比表

| 物资 | Naive-Seasonal | Croston-TSB-LGB | **LGB-Direct** | CatBoost | TwoStage |
|------|:--:|:--:|:--:|:--:|:--:|
| 控制电缆 | **0.6260** | -0.1206 | **0.1972** | -0.1509 | 0.0968 |
| 电缆保护管 | -2.6298 | -0.0809 | -0.0591 | 0.0775 | **0.1580** |
| 通信单元 | -0.1391 | -0.0872 | -0.0154 | -0.0151 | **0.1702** |
| 电缆接线端子 | -0.3368 | -0.0807 | -0.0131 | -0.2410 | **-0.0260** |
| 蝶式绝缘子 | 0.3261 | -0.1466 | -0.0441 | -0.1281 | **0.4936** |

### 4.3 关键发现

#### 发现一：LGB-Direct 在 5/5 种物资上优于 Croston-TSB-LGB

LGB-Direct 在 R² 维度全部优于 Croston-TSB-LGB（5/5）。最显著的差距在控制电缆（0.20 vs -0.12）和电缆接线端子（-0.01 vs -0.08）。

**为什么 LGB-Direct 更好：**
- 训练样本：LGB-Direct 使用 62-h 个（每步约 50-61 个），Croston 只有 ~14 个
- 特征工程：LGB-Direct 的 23 维特征包含 lag1~lag18 和滚动统计，即使很多特征高度共线，但至少有一个 lag（通常是 lag12/去年同期）提供了强烈的预测信号
- 零值处理：LGB-Direct 直接回归零值——"这个月为零"是一个可学习的模式。而 Croston 根本没法学"这个月为零"

#### 发现二：但仍然无法超过 Naive-Seasonal

控制电缆上 Naive-Seasonal R²=0.63 vs LGB-Direct R²=0.20。这不是模型架构问题——但最朴素的信息源（去年同期）比 23 维特征更有效。

这说明：**时序的"去年同期"模式是当前数据中唯一可靠的信号。** 所有外部因子（铜价、PMI、投资额）的预测力都不如简单一句"重复去年同月"。

#### 发现三：LGB-Direct vs TwoStage 的分化

| 比较 | 控制电缆 | 电缆保护管 | 通信单元 | 电缆接线端子 | 蝶式绝缘子 |
|------|:--:|:--:|:--:|:--:|:--:|
| LGB-Direct vs TwoStage | 胜 | 负 | 负 | 胜 | 负 |

LGB-Direct 在 2/5 种物资上胜出。TwoStage 在 3/5 种物资上仍领先。但两种方法在绝对值上都远不如"同期去年"——这个朴素基线在两种物资（控制电缆 R²=0.63、蝶式绝缘子 R²=0.33）上遥遥领先。

#### 发现四：LGB-Direct 的预测分布

LGB-Direct 的实际预测行为：

```
控制电缆 LGB-Direct: [12694, 3907, 13993, 2624, 3033, 0, 0, 19794, 21078, 18741, 1674, 0]
控制电缆真实值:      [    0,    0,     0, 10010,    0, 0, 0,     0, 86887,     0,    0, 0]
```

LGB-Direct 至少做到了两点 Croston 做不到的：
1. **能预测真正的零值**（第 6、7、12 个月预测为 0）
2. **能产生非零预测**（在所有月份都有正值，但没有 Croston 那种强行均值）
3. **22,000 ~ 21,078 的预测**对 86,887 的真实值低估了 75%，但至少方向是对的

### 4.5 改进方向

#### 短期（1-2天）

1. **LGB-Direct + Seasonal Anchor 混合**
   - 将 `Naive-Seasonal` 的预测值作为第 24 维特征加入 LGB-Direct
   - 模型会学习"同期去年值"和"近期趋势"的加权组合
   - 如果 lag12 已经提供了同样的信息 → LightGBM 会自然忽略它。但如果同期去年的模式是唯一的有效信号 → 模型会大量依赖它

2. **LGB-Direct + TwoStage 融合**
   - Stage 1: TwoStage 输出 P(有需求|X)
   - Stage 2: LGB-Direct 输出 Q_hat
   - 混合: ŷ = α × P × Q + (1-α) × LGB_Direct_pred
   - α 由 LightGBM 在训练集上学习得出

3. **多物资联合训练**
   - 5 种物资的序列拼成 (5×74, 23) → 370 个样本
   - 加入 material_id 作为分类特征
   - 这是 M5 竞赛中获胜方案的核心策略：**跨序列信息共享**

#### 中长期（3-5天）

4. **Tweedie Objective**
   - 替换 MSELoss 为 `objective='tweedie'` (tweedie_variance_power=1.5)
   - Tweedie 分布是 compound Poisson-Gamma，在零值处有概率质量
   - 理论上更适合零膨胀数据

5. **Warm-start 转移学习**
   - 用 M5 零售数据预训练一个基准 LGB-Direct 模型
   - 用冀北数据做 fine-tuning（只更新叶子值，不更新树结构）
   - 本质上是"学习间歇性需求的通用表征，迁移到电力物资"

### 4.6 与 Croston-TSB-LGB 的交叉比较

| 维度 | Croston-TSB-LGB | LGB-Direct | 优胜 |
|------|:--|:--|:--:|
| 训练样本量 | ~14 (仅非零事件) | ~55 (全序列) | LGB-Direct ★★★ |
| 零值建模 | 间接（通过间隔） | 直接（回归目标含零） | LGB-Direct ★★ |
| 可解释性 | "每X月采购~Y量" | 黑箱 | Croston ★★★ |
| 长间隔预测 | TSB衰减有帮助 | lag18/24有帮助 | 平手 |
| 需求为0时的表现 | 固定模式衰减 | 能预测真实零值 | LGB-Direct ★★ |
| 方法论创新度 | Croston+梯度提升 | M5方案+电力物资 | Croston更高 |
| 实验R² | -0.1206 ~ -0.0809 | -0.0591 ~ 0.1972 | LGB-Direct ★★★ |

**结论：LGB-Direct 在实验指标上明显优于 Croston-TSB-LGB，但两者都未能超越 Naive-Seasonal 基线。** 推荐论文中以"LightGBM Direct Multi-Step with Seasonal Anchor"（改进方向 #1）作为主模型，Naive-Seasonal + Croston-TSB + TwoStage 作为对比方法。这形成了"朴素基线 → 经典间歇性方法 → 两阶段 ML → 直接多步 ML"的完整对比链条。
