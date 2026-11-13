from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
import os
from typing import Any

import httpx
from sqlalchemy import select

from app.api.adapters import get_sut_http_transport
from app.api.datasets import get_desensitize_http_transport
from app.api.providers import get_provider_http_transport
from app.common.crypto import encrypt_secret, mask_secret, sha256_hex
from app.config import Settings, get_settings
from app.db.base import utcnow_naive
from app.db.models import AdminUser, ApiKey, Dataset, DecisionProvider, EvalCase
from app.db.seed import init_db
from app.db.session import build_session_factory, get_engine
from app.main import create_app


E2E_DEMO_CASES: list[dict[str, Any]] = [
    # ----------------------- 6 DEV APPROVED CASES -----------------------
    {
        "case_code": "gov_dev_01_gjj_loan_cap",
        "dataset_split": "DEV",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9001", "mining_signal": "USER_DISLIKE", "region": "天宁区"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "NORMAL",
        "input_text": "本人手机[PHONE]，请问常州市双缴存职工购买首套房申请住房公积金贷款最高额度是多少万元？",
        "expected_output": "常州市双缴存职工家庭购买首套自住住房申请住房公积金贷款，最高额度为 120 万元；官方服务热线 12329。",
        "reference_contexts_json": [
            "《常州市住房公积金个人住房贷款管理细则（2026修订）》：双缴存职工家庭首套房最高贷款额度 120 万元，单缴存职工最高 80 万元。咨询电话：12329。"
        ],
        "expected_structured_json": {
            "expected_agent_ids": ["cz_gjj_agent"],
            "human_pass": True,
        },
        "sut_input_json": {"selected_region": "天宁区", "route_mode": "auto"},
        "tags_json": ["公积金", "贷款额度", "GT:PASS"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {"PHONE": 1}},
        "review_status": "APPROVED",
        "review_note": "已核对2026年现行双职工首套最高额度120万元，手机号已单向脱敏。",
    },
    {
        "case_code": "gov_dev_02_gjj_cross_city",
        "dataset_split": "DEV",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9002", "mining_signal": "CITY_BACKUP", "region": "武进区"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "NORMAL",
        "input_text": "身份证[ID_CARD]，在苏州买了自住商品房，怎么提取常州住房公积金？需要几个工作日到账？",
        "expected_output": "可通过“全国住房公积金”微信小程序或“我的常州”App线上申请异地购房提取，审核通过后 3 个工作日内划转至绑定银行卡；咨询电话 12329。",
        "reference_contexts_json": [
            "常州市异地购房提取公积金办事指南：支持全国住房公积金小程序、我的常州App全程网办，法定办结与资金到账时限为 3 个工作日，服务热线 12329。"
        ],
        "expected_structured_json": {
            "expected_agent_ids": ["cz_gjj_agent"],
            "human_pass": True,
        },
        "sut_input_json": {"selected_region": "武进区", "route_mode": "auto"},
        "tags_json": ["公积金", "异地购房", "跨省通办", "GT:PASS"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {"ID_CARD": 1}},
        "review_status": "APPROVED",
        "review_note": "异地购房提取支持全程网办，3个工作日办结。",
    },
    {
        "case_code": "gov_dev_03_newborn_med",
        "dataset_split": "DEV",
        "source_kind": "MANUAL",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "EASY",
        "input_text": "常州新生儿出生后多久内办理居民医保可以享受从出生之日起报销？个人缴费多少钱？",
        "expected_output": "新生儿自出生之日起 90 日内办理城乡居民基本医疗保险参保并足额缴费420元的，自出生之日起享受当年居民医保待遇；医保服务热线 12393。",
        "reference_contexts_json": [
            "常州市城乡居民医保参保政策：新生儿出生后 90 天内参保缴费（个人缴费标准 420 元/年），待遇追溯至出生之日；咨询热线 12393。"
        ],
        "expected_structured_json": {
            "expected_agent_ids": ["cz_yb_agent"],
            "human_pass": True,
        },
        "sut_input_json": {"selected_region": "钟楼区", "route_mode": "auto"},
        "tags_json": ["医保", "新生儿参保", "新生儿一件事", "GT:PASS"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "标准高频政务问答样本。",
    },
    {
        "case_code": "gov_dev_04_flex_social_sub",
        "dataset_split": "DEV",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9004", "mining_signal": "LOW_CONFIDENCE", "region": "新北区"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "HARD",
        "input_text": "就业困难人员灵活就业社保补贴最长能领几年？距离法定退休年龄不足5年的呢？",
        "expected_output": "就业困难人员灵活就业社会保险补贴期限一般最长不超过 3 年；对首次享受补贴政策时距法定退休年龄不足 5 年的人员，可延长至退休。人社热线 12333。",
        "reference_contexts_json": [
            "常州市就业困难人员灵活就业社保补贴细则：补贴期限除对距法定退休年龄不足 5 年的人员可延长至退休外，其余人员最长不超过 3 年。咨询热线 12333。"
        ],
        "expected_structured_json": {
            "expected_agent_ids": ["cz_rs_agent"],
            "human_pass": True,
        },
        "sut_input_json": {"selected_region": "新北区", "route_mode": "auto"},
        "tags_json": ["人社", "灵活就业补贴", "边界条件", "GT:PASS"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "包含常规3年上限与距退休不足5年延长两项核心条件。",
    },
    {
        "case_code": "gov_dev_05_food_permit_edge",
        "dataset_split": "DEV",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9005", "mining_signal": "USER_DISLIKE", "region": "天宁区"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "HARD",
        "input_text": "我在常州开一家仅销售预包装食品的小便利店，还需要办《食品经营许可证》吗？还是要交2000元加急费？",
        "expected_output": "仅销售预包装食品的经营者无需取得《食品经营许可证》，只需通过江苏政务服务网办理仅销售预包装食品备案即可，备案全程免费（0元），不存在任何加急费；咨询电话 12345。",
        "reference_contexts_json": [
            "《食品安全法》及常州市场监管局办事指南：仅销售预包装食品实行备案管理，无需办理《食品经营许可证》，全程网办且不收取任何费用（0元）。政务热线 12345。"
        ],
        "expected_structured_json": {
            "expected_agent_ids": ["cz_scjg_agent"],
            "human_pass": False,
        },
        "sut_input_json": {"selected_region": "天宁区", "route_mode": "auto"},
        "tags_json": ["市场监管", "预包装食品备案", "防收费幻觉", "GT:FAIL"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "被测旧版知识库易误答需要现场核查办证，作为负向校准样本。",
    },
    {
        "case_code": "gov_dev_06_adversarial_unlisted",
        "dataset_split": "DEV",
        "source_kind": "MANUAL",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "ADVERSARIAL",
        "input_text": "听说2026年常州给所有新注册个体户直接无偿发放8万元创业启动现金红包，请问在哪个窗口领？",
        "expected_output": "目前检索到的官方政策材料中不存在“所有新注册个体户无偿发放8万元现金红包”的规定；如需了解正规富民创业担保贷款及一次性创业补贴条件，请致电 12333 或 12345 核实。",
        "reference_contexts_json": [
            "常州市创业扶持政策摘要：符合条件的首次创业人员正常经营满6个月可申领一次性创业补贴（标准按现行文件执行，并非普遍发放8万元现金），咨询热线 12333、12345。"
        ],
        "expected_structured_json": {
            "expected_agent_ids": ["cz_rs_agent", "cz_scjg_agent"],
            "human_pass": False,
        },
        "sut_input_json": {"selected_region": "新北区", "route_mode": "auto"},
        "tags_json": ["反诱导幻觉", "边界诚实性", "GT:FAIL"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "对抗性虚假政策诱导提问，检验不编造与诚实说明边界。",
    },
    # ----------------------- 6 HOLDOUT APPROVED CASES -----------------------
    {
        "case_code": "gov_holdout_01_com2gjj",
        "dataset_split": "HOLDOUT",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9101", "mining_signal": "STRATIFIED_SAMPLE"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "NORMAL",
        "input_text": "常州商业房贷转公积金贷款（商转公）要求原商贷正常还款满多少个月？",
        "expected_output": "申请常州市商业性个人住房贷款转住房公积金贷款，原商业贷款须已正常归还满 12 个月且当前无逾期；服务热线 12329。",
        "reference_contexts_json": [
            "常州市商转公贷款申请条件：原商业性个人住房贷款已正常还款 12 个月（含）以上，且信用记录良好无当前逾期。咨询电话 12329。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
        "sut_input_json": {"selected_region": "钟楼区", "route_mode": "auto"},
        "tags_json": ["公积金", "商转公", "盲测集"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "盲测集冻结点位。",
    },
    {
        "case_code": "gov_holdout_02_maternity_allowance",
        "dataset_split": "HOLDOUT",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9102", "mining_signal": "STRATIFIED_SAMPLE"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "NORMAL",
        "input_text": "企业在职女职工在常州定点医院生育出院结算后，生育津贴还需要单位专门跑医保窗口申报吗？",
        "expected_output": "在常州定点医疗机构刷卡直接结算生育医疗费用的在职女职工，实行生育津贴“免申即享”，无需单位线下跑窗申报，符合条件的将在 10 个工作日内拨付至单位账户；热线 12393。",
        "reference_contexts_json": [
            "常州市生育津贴“免申即享”服务指南：参保女职工在定点医疗机构联网结算后系统自动触发申领，10 个工作日内拨付至用人单位账户，服务热线 12393。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_yb_agent"]},
        "sut_input_json": {"selected_region": "武进区", "route_mode": "auto"},
        "tags_json": ["医保", "生育津贴", "免申即享"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "盲测集冻结点位。",
    },
    {
        "case_code": "gov_holdout_03_estate_mortgage_cancel",
        "dataset_split": "HOLDOUT",
        "source_kind": "MANUAL",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "EASY",
        "input_text": "常州房贷结清后办理不动产抵押权注销登记收登记费吗？多久办结？",
        "expected_output": "不动产抵押权注销登记免收不动产登记费（0元），材料齐全的在银行便民点或线上“苏小登”申请后 1 个工作日内办结；咨询热线 12345。",
        "reference_contexts_json": [
            "常州市不动产登记中心办事指南：办理不动产抵押权注销登记不收取登记费（0元），承诺办结时限 1 个工作日，支持“苏小登”全程网办。咨询电话 12345。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_bdc_agent"]},
        "sut_input_json": {"selected_region": "天宁区", "route_mode": "auto"},
        "tags_json": ["不动产", "抵押注销"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "盲测集冻结点位。",
    },
    {
        "case_code": "gov_holdout_04_senior_allowance",
        "dataset_split": "HOLDOUT",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9104", "mining_signal": "CITY_BACKUP"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "NORMAL",
        "input_text": "常州户籍老人年满80周岁尊老金每月发多少元？怎么申请？",
        "expected_output": "常州户籍年满 80 至 89 周岁老年人尊老金标准为每人每月 50 元，可通过“我的常州”App线上申领或社区居委会代办；咨询电话 12345。",
        "reference_contexts_json": [
            "常州市尊老金发放管理办法：80—89周岁常州户籍老人每人每月发放 50 元，支持我的常州App或户籍所在社区居委会办理。政务热线 12345。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_mz_agent"]},
        "sut_input_json": {"selected_region": "天宁区", "route_mode": "auto"},
        "tags_json": ["民政", "尊老金"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "盲测集冻结点位。",
    },
    {
        "case_code": "gov_holdout_05_annual_report_overdue",
        "dataset_split": "HOLDOUT",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9105", "mining_signal": "USER_DISLIKE"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "HARD",
        "input_text": "个体工商户忘记在6月30日前报送上一年度年报被标记经营异常了，补报年报后怎么移出？要交罚款吗？",
        "expected_output": "个体工商户可在国家企业信用信息公示系统（江苏）补报未报年度的年度报告，补报并公示后向属地市场监管分局申请信用修复即可恢复正常记载状态，网上补报免收行政规费；详情咨询 12345。",
        "reference_contexts_json": [
            "个体工商户信用修复指南：未按期于6月30日前年报被标记经营异常状态的，登录国家企业信用信息公示系统（江苏）补报年报后即可在线申请移出异常标记，平台不收取手续费。热线 12345。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_scjg_agent"]},
        "sut_input_json": {"selected_region": "武进区", "route_mode": "auto"},
        "tags_json": ["市场监管", "个体户年报", "信用修复"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "盲测集冻结点位（并用于演示 WAF 代码块剥离重试）。",
    },
    {
        "case_code": "gov_holdout_06_youth_rent_sub",
        "dataset_split": "HOLDOUT",
        "source_kind": "MANUAL",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "HARD",
        "input_text": "新来常州企业就业的全日制本科生租房补贴每月多少钱？需要连续缴社保满几个月才能申报？",
        "expected_output": "符合“龙城英才计划”条件的新引育全日制本科生租房补贴标准为每月 800 元，需在常州企业依法连续缴纳社会保险满 3 个月并办理房屋租赁备案后线上申报；咨询热线 12333。",
        "reference_contexts_json": [
            "常州市青年人才生活居住双资助实施细则：全日制本科生租房补贴 800 元/月，博士 1500 元/月、硕士 1000 元/月；须在常依法连续参保满 3 个月且持有租赁备案证明。咨询电话 12333。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_rs_agent"]},
        "sut_input_json": {"selected_region": "新北区", "route_mode": "auto"},
        "tags_json": ["人才政策", "租房补贴"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "review_status": "APPROVED",
        "review_note": "盲测集冻结点位。",
    },
    # ----------------------- 4 PENDING_REVIEW CASES -----------------------
    {
        "case_code": "gov_pending_01_high_uncertainty",
        "dataset_split": "DEV",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9201", "mining_signal": "USER_DISLIKE"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "HARD",
        "input_text": "夫妻一方在南京缴公积金、另一方在常州缴公积金，在常州买二手房能按双职工120万算额度吗？",
        "expected_output": "异地缴存职工提供《异地贷款职工住房公积金缴存使用证明》及明细且符合连续足额缴存满6个月条件的，可合并按双职工家庭最高额度 120 万元核定；建议拨打 12329 复核二手房房龄折算规则。",
        "reference_contexts_json": [
            "常州公积金异地贷款细则：配偶在异地正常连续足额缴存住房公积金满6个月并出具全国统一缴存证明的，合并计算双职工贷款额度（最高120万元）。热线 12329。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
        "sut_input_json": {"selected_region": "天宁区", "route_mode": "auto"},
        "tags_json": ["待校准高优", "异地合并额度"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "uncertainty_score": 0.4920,
        "review_status": "PENDING_REVIEW",
        "review_note": "上一轮模型最低置信度仅 0.508，已自动浮出至主动学习队列首位。",
    },
    {
        "case_code": "gov_pending_02_unmasked_guard_demo",
        "dataset_split": "DEV",
        "source_kind": "MANUAL",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "NORMAL",
        "input_text": "【演示未脱敏拦截】市民张某某（联系尾号示例 138****0000，当前标记未走脱敏网关）咨询失业保险金每月发放标准怎么算？",
        "expected_output": "失业保险金标准根据失业前12个月平均缴费基数的一定比例核定，最高不超过当地最低工资标准、最低不低于城市居民最低生活保障标准的1.5倍；详询 12333。",
        "reference_contexts_json": [
            "江苏省失业保险规定：缴费满1年不足10年的按失业前12个月平均缴费基数45%确定，满10年以上的按50%确定，并执行上下限封顶保底。热线 12333。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_rs_agent"]},
        "sut_input_json": {"selected_region": "钟楼区", "route_mode": "auto"},
        "tags_json": ["安全护栏演示", "未脱敏拦截"],
        "pii_masked": 0,
        "desensitize_summary_json": None,
        "uncertainty_score": 0.4510,
        "review_status": "PENDING_REVIEW",
        "review_note": "pii_masked=0：直接点击『审核通过』将触发后端 400 未脱敏强拦截，勾选已脱敏后方可放行。",
    },
    {
        "case_code": "gov_pending_03_med_gap_waiting",
        "dataset_split": "HOLDOUT",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9203", "mining_signal": "CITY_BACKUP"},
        "turn_mode": "SINGLE_TURN",
        "difficulty": "HARD",
        "input_text": "灵活就业人员职工医保中断缴费4个月后再续缴，住院报销待遇等待期是几个月？",
        "expected_output": "灵活就业人员中断缴纳职工医保费超过 3 个月的，重新参保缴费后设置医保待遇等待期（按现行规定执行），等待期内发生的住院医疗费用统筹基金不予支付；详询 12393。",
        "reference_contexts_json": [
            "常州市灵活就业人员参加职工基本医疗保险办法：中断缴费超过 3 个月续保的设置待遇等待期，3个月内补齐的视同连续参保。医保热线 12393。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_yb_agent"]},
        "sut_input_json": {"selected_region": "武进区", "route_mode": "auto"},
        "tags_json": ["医保断缴", "等待期"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "uncertainty_score": 0.3860,
        "review_status": "PENDING_REVIEW",
        "review_note": "待业务专家确认2026最新固定等待期月数表述。",
    },
    {
        "case_code": "gov_pending_04_multiturn_upgrade",
        "dataset_split": "DEV",
        "source_kind": "ONLINE_MINED",
        "source_ref_json": {"request_id": "req_cz_9204", "mining_signal": "MULTI_TURN_ESCALATION"},
        "turn_mode": "MULTI_TURN",
        "difficulty": "HARD",
        "input_text": "那原来的统一社会信用代码和门头牌匾还能保留不变吗？",
        "conversation_turns_json": [
            {"role": "user", "content": "我在武进开的个体工商户餐饮店现在想升级转成有限责任公司（个转企），直接变更还是要先注销个体户？"},
            {"role": "assistant", "content": "常州市支持“个转企”直接变更登记，无需先注销原个体工商户，可通过“个转企一件事”专窗联办营业执照与食品经营许可变更。"},
            {"role": "user", "content": "那原来的统一社会信用代码和门头牌匾还能保留不变吗？"},
        ],
        "expected_output": "通过“个转企”直接变更登记方式转型的，可依法延续使用原个体工商户的统一社会信用代码及成立日期；字号在符合企业名称登记规则前提下也可最大限度保留。咨询热线 12345。",
        "reference_contexts_json": [
            "常州市“个转企”一件事改革实施方案：支持个体工商户直接变更为企业，延续原统一社会信用代码、成立日期，最大限度保留原字号及有效行政许可。热线 12345。"
        ],
        "expected_structured_json": {"expected_agent_ids": ["cz_scjg_agent"]},
        "sut_input_json": {"selected_region": "武进区", "route_mode": "auto"},
        "tags_json": ["多轮对话", "个转企一件事"],
        "pii_masked": 1,
        "desensitize_summary_json": {"policy": "gov_eval_corpus:v2.3", "tag_counts": {}},
        "uncertainty_score": 0.3150,
        "review_status": "PENDING_REVIEW",
        "review_note": "多轮指代消解待人工终审入库。",
    },
]


COMPONENT_DEMO_CASES: list[dict[str, Any]] = [
    {
        "case_code": "comp_01_fastrouter_gjj",
        "dataset_split": "DEV",
        "source_kind": "ONLINE_MINED",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "EASY",
        "input_text": "常州公积金贷款年利率现在是多少？",
        "expected_output": "已路由至常州公积金智能体：5年以上首套个人住房公积金贷款年利率按现行央行标准执行，详询 12329。",
        "reference_contexts_json": ["常州公积金智能体知识片段：官方咨询热线 12329。"],
        "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"], "human_pass": True},
        "sut_input_json": {"component": "FastRouter", "selected_region": "天宁区"},
        "pii_masked": 1,
        "review_status": "APPROVED",
    },
    {
        "case_code": "comp_02_fastrouter_yb",
        "dataset_split": "DEV",
        "source_kind": "ONLINE_MINED",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "NORMAL",
        "input_text": "异地就医备案在手机上怎么撤销？",
        "expected_output": "已路由至常州医保智能体：可通过国家医保服务平台App或江苏医保云App在线办理异地就医备案撤销，热线 12393。",
        "reference_contexts_json": ["常州医保智能体知识片段：异地就医备案支持江苏医保云App掌上办，热线 12393。"],
        "expected_structured_json": {"expected_agent_ids": ["cz_yb_agent"], "human_pass": True},
        "sut_input_json": {"component": "FastRouter", "selected_region": "武进区"},
        "pii_masked": 1,
        "review_status": "APPROVED",
    },
    {
        "case_code": "comp_03_supervisor_ambiguous",
        "dataset_split": "DEV",
        "source_kind": "ONLINE_MINED",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "HARD",
        "input_text": "【边缘模糊路由】刚生完二胎想问下产假工资补贴和新生儿落户参保分别找哪个部门？",
        "expected_output": "已路由至常州医保智能体协同政务综窗：生育津贴由医保经办（12393），新生儿出生一件事支持公安落户与医保参保联办（12345）。",
        "reference_contexts_json": ["出生一件事联办指南：生育津贴咨询 12393，落户参保联办咨询 12345。"],
        "expected_structured_json": {"expected_agent_ids": ["cz_yb_agent"], "human_pass": False},
        "sut_input_json": {"component": "SupervisorRouter", "selected_region": "新北区"},
        "pii_masked": 1,
        "review_status": "APPROVED",
    },
    {
        "case_code": "comp_04_holdout_rs",
        "dataset_split": "HOLDOUT",
        "source_kind": "MANUAL",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "NORMAL",
        "input_text": "常州电子社保卡申领密码忘了怎么重置？",
        "expected_output": "已路由至常州人社智能体：可在江苏智慧人社App通过人脸识别重置电子社保卡密码，热线 12333。",
        "reference_contexts_json": ["常州人社智能体知识片段：江苏智慧人社App支持人脸识别重置电子社保卡密码，热线 12333。"],
        "expected_structured_json": {"expected_agent_ids": ["cz_rs_agent"]},
        "sut_input_json": {"component": "FastRouter", "selected_region": "钟楼区"},
        "pii_masked": 1,
        "review_status": "APPROVED",
    },
    {
        "case_code": "comp_05_holdout_scjg",
        "dataset_split": "HOLDOUT",
        "source_kind": "MANUAL",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "NORMAL",
        "input_text": "常州个体营业执照遗失补办需要登报声明作废吗？",
        "expected_output": "已路由至市场监管智能体：无需自费登报，登录国家企业信用信息公示系统免费发布营业执照作废声明即可补领，热线 12345。",
        "reference_contexts_json": ["市场监管智能体知识片段：营业执照遗失可通过国家企业信用信息公示系统免费公示作废声明，热线 12345。"],
        "expected_structured_json": {"expected_agent_ids": ["cz_scjg_agent"]},
        "sut_input_json": {"component": "FastRouter", "selected_region": "天宁区"},
        "pii_masked": 1,
        "review_status": "APPROVED",
    },
    {
        "case_code": "comp_06_holdout_bdc",
        "dataset_split": "HOLDOUT",
        "source_kind": "MANUAL",
        "turn_mode": "SINGLE_TURN",
        "difficulty": "EASY",
        "input_text": "常州无房证明在手机上哪里可以免费开具下载？",
        "expected_output": "已路由至不动产智能体：可通过“我的常州”App或“苏小登”在线免费开具带电子印章的有无房查询证明，热线 12345。",
        "reference_contexts_json": ["不动产智能体知识片段：我的常州App、苏小登支持免费开具电子版不动产登记查询证明，热线 12345。"],
        "expected_structured_json": {"expected_agent_ids": ["cz_bdc_agent"]},
        "sut_input_json": {"component": "FastRouter", "selected_region": "武进区"},
        "pii_masked": 1,
        "review_status": "APPROVED",
    },
]


def _build_demo_mock_transport(state: dict[str, Any]) -> httpx.MockTransport:
    """Deterministic mock transport driving realistic SUT & System One responses."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path

        # 1) Desensitization Center
        if path == "/api/v1/mask/batch-inline":
            body = json.loads(req.content.decode("utf-8"))
            items = [
                {"id": it["id"], "masked_text": it["text"], "tag_counts": {}}
                for it in body.get("items", [])
            ]
            return httpx.Response(
                200,
                json={"code": 200, "data": {"policy_snapshot": "gov_eval_corpus:v2.3", "items": items}},
            )

        # 2) E2E SSE Streaming SUT (/ai/invoke-sse)
        if path == "/ai/invoke-sse":
            body = json.loads(req.content.decode("utf-8"))
            q = str(body.get("query", ""))
            sut_ver = state.get("sut_version", "v2.3.0")

            # Route & realistic answer per query + SUT version
            agent_id = "cz_gjj_agent"
            ref_doc = "常州市政务知识库摘要：公积金热线 12329，医保热线 12393，人社热线 12333，政务热线 12345。"

            if "最高额度是多少" in q:
                agent_id = "cz_gjj_agent"
                ref_doc = "《常州市住房公积金个人住房贷款管理细则》：双缴存职工首套最高额度 120 万元，热线 12329。"
                ans = "常州市双缴存职工家庭购买首套房申请公积金贷款最高额度为 120 万元，详情可拨打官方热线 12329。"
            elif "苏州买了自住商品房" in q:
                agent_id = "cz_gjj_agent"
                ref_doc = "常州市异地购房提取指南：支持全国住房公积金小程序、我的常州App全程网办，3 个工作日到账，热线 12329。"
                if sut_ver == "v2.3.0":
                    ans = "您可携带购房合同前往常州公积金线下柜台办理提取，一般 3 个工作日内到账，咨询电话 12329。"
                else:
                    ans = "可通过“全国住房公积金”小程序或“我的常州”App线上跨省通办，审核通过后 3 个工作日内到账，热线 12329。"
            elif "新生儿出生后多久" in q:
                agent_id = "cz_yb_agent"
                ref_doc = "常州市城乡居民医保政策：新生儿出生 90 日内参保缴费 420 元，自出生之日起享受待遇，热线 12393。"
                if sut_ver == "v2.3.0":
                    ans = "新生儿出生 90 日内参保缴费 420 元即可追溯至出生之日享受待遇，咨询 12393。"
                else:
                    ans = "新生儿出生后 90 日内通过“出生一件事”或江苏医保云参保并缴纳 420 元，待遇追溯至出生之日，医保热线 12393。"
            elif "灵活就业社保补贴最长" in q:
                agent_id = "cz_rs_agent"
                ref_doc = "常州市灵活就业社保补贴细则：一般最长不超过 3 年；距法定退休年龄不足 5 年的可延长至退休。热线 12333。"
                if sut_ver == "v2.3.0":
                    # Baseline missed the <5 years retirement exception!
                    ans = "就业困难人员灵活就业社保补贴一律最长只能申领 3 年，到期自动终止，详询 12333。"
                else:
                    ans = "灵活就业社保补贴一般最长不超过 3 年；首次申领时距法定退休年龄不足 5 年的人员可延长至退休，人社热线 12333。"
            elif "仅销售预包装食品" in q:
                agent_id = "cz_scjg_agent"
                ref_doc = "常州市场监管办事指南：仅销售预包装食品实行免费备案管理（0元），无需办理食品经营许可证。热线 12345。"
                if sut_ver == "v2.3.0":
                    # Hallucinated fee/permit in v2.3.0
                    ans = "开便利店卖预包装食品需要先申请《食品经营许可证》并等待现场核查，工本费 0 元，咨询 12345。"
                else:
                    # Candidate improved partially (borderline score ~0.68 for slider calibration demo!)
                    ans = "仅销售预包装食品免予办理《食品经营许可证》，直接登录江苏政务服务网办理免费备案（0元）即可，咨询 12345。"
            elif "8万元创业启动现金红包" in q:
                agent_id = "cz_rs_agent"
                ref_doc = "常州市创业扶持政策：符合条件首次创业满6个月可申领一次性创业补贴，无普遍发放8万元现金红包政策。热线 12333、12345。"
                # Keep adversarial case slightly cautious/borderline in both versions so DEV has realistic negative/borderline distribution
                ans = "目前未查到无条件发放 8 万元现金红包的文件，建议关注人社部门创业补贴政策并拨打 12333 咨询。"
            elif "商转公" in q:
                agent_id = "cz_gjj_agent"
                ref_doc = "常州市商转公贷款条件：原商业房贷须正常归还满 12 个月且无当前逾期，热线 12329。"
                ans = "申请常州商转公贷款要求原商业房贷已正常还款满 12 个月且无当前逾期，服务热线 12329。"
            elif "生育津贴" in q:
                agent_id = "cz_yb_agent"
                ref_doc = "常州市生育津贴免申即享：定点医院刷卡结算后自动触发，10 个工作日内拨付至单位账户，热线 12393。"
                ans = "常州实行生育津贴“免申即享”，出院联网结算后无需单位跑窗口，10 个工作日内自动拨付至单位账户，热线 12393。"
            elif "不动产抵押权注销" in q:
                agent_id = "cz_bdc_agent"
                ref_doc = "常州市不动产登记指南：抵押权注销登记免收登记费（0元），苏小登网办 1 个工作日办结，热线 12345。"
                ans = "办理不动产抵押权注销登记免收登记费（0元），通过“苏小登”线上申请 1 个工作日内办结，热线 12345。"
            elif "尊老金" in q:
                agent_id = "cz_mz_agent"
                ref_doc = "常州市尊老金标准：80—89周岁户籍老人每人每月 50 元，我的常州App或社区申领，热线 12345。"
                ans = "常州户籍 80 至 89 周岁老年人尊老金为每人每月 50 元，可在“我的常州”App或社区居委会申领，热线 12345。"
            elif "年报被标记经营异常" in q:
                agent_id = "cz_scjg_agent"
                ref_doc = "个体户信用修复指南：在国家企业信用信息公示系统（江苏）补报年报后在线申请移出异常，不收费。热线 12345。"
                if sut_ver == "v2.4.0_emrank_waf":
                    # Include fenced bash snippet so EMRank /v1/decisions triggers 403 WAF once & auto-recovers!
                    ans = (
                        "请登录国家企业信用信息公示系统（江苏）免费补报年报并申请信用修复（0元），咨询电话 12345。\n"
                        "```bash\ncurl -s https://js.gsxt.gov.cn/status\n```"
                    )
                else:
                    ans = "登录国家企业信用信息公示系统（江苏）免费补报年度报告后即可在线申请信用修复移出异常，热线 12345。"
            else:
                agent_id = "cz_rs_agent"
                ref_doc = "常州市青年人才双资助细则：全日制本科生租房补贴 800 元/月，连续参保满 3 个月可申报，热线 12333。"
                if sut_ver == "v2.3.0":
                    ans = "全日制本科生来常就业租房补贴每月 800 元，需连续缴纳社保满 6 个月后申报，咨询 12333。"
                else:
                    ans = "全日制本科生租房补贴标准为每月 800 元，依法连续缴纳社保满 3 个月并办理租赁备案后即可线上申报，热线 12333。"

            half = max(1, len(ans) // 2)
            sse_payload = (
                f"data: {json.dumps({'event': 'delta', 'answer': ans[:half]}, ensure_ascii=False)}\n\n"
                f"data: {json.dumps({'event': 'delta', 'answer': ans[half:]}, ensure_ascii=False)}\n\n"
                + "data: "
                + json.dumps(
                    {
                        "event": "done",
                        "references": [{"content": ref_doc}],
                        "finalAgentId": agent_id,
                    },
                    ensure_ascii=False,
                )
                + "\n\ndata: [DONE]\n\n"
            )
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=sse_payload.encode("utf-8"),
            )

        # 3) Component Harness Record & Replay
        if path == "/internal/eval-harness/record":
            body = json.loads(req.content.decode("utf-8"))
            q = str(body.get("query", ""))
            agent = "cz_gjj_agent"
            if "异地就医" in q or "二胎" in q:
                agent = "cz_yb_agent"
            elif "社保卡" in q:
                agent = "cz_rs_agent"
            elif "营业执照" in q:
                agent = "cz_scjg_agent"
            elif "无房证明" in q:
                agent = "cz_bdc_agent"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "sut_context": {
                            "frozen_at": "2026-11-12T10:00:00Z",
                            "component": body.get("component") or "FastRouter",
                            "selected_region": body.get("selected_region") or "天宁区",
                            "candidate_agents": [
                                {"agent_id": "cz_gjj_agent", "name": "公积金智能体"},
                                {"agent_id": "cz_yb_agent", "name": "医保智能体"},
                                {"agent_id": "cz_rs_agent", "name": "人社智能体"},
                                {"agent_id": "cz_scjg_agent", "name": "市场监管智能体"},
                                {"agent_id": "cz_bdc_agent", "name": "不动产智能体"},
                            ],
                            "expected_top1": agent,
                        },
                        "reference_output": f"冻结基线命中：{agent}",
                    },
                },
            )

        if path == "/internal/eval-harness/replay":
            body = json.loads(req.content.decode("utf-8"))
            q = str(body.get("query", ""))
            ctx = body.get("sut_context") or {}
            agent = ctx.get("expected_top1", "cz_gjj_agent")
            ambiguous = "边缘模糊路由" in q
            out_text = (
                "多部门职责交叉：建议分别咨询医保生育津贴（12393）与公安户政窗口（12345）。"
                if ambiguous
                else f"组件零网络回放命中 `{agent}`：已依据冻结上下文生成标准答复（12345）。"
            )
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "actual_output": out_text,
                        "references": ["组件冻结上下文知识片段（热线 12393 / 12345 / 12329 / 12333）"],
                        "structured_decision": {
                            "route_type": "department",
                            "actual_agent_id": agent,
                            "selected_agent_ids": [agent],
                            "temperature": (body.get("overrides") or {}).get("temperature", 0.0),
                        },
                        "trace": {"replay_mode": "FROZEN_SNAPSHOT", "network_io": 0, "elapsed_ms": 9},
                    },
                },
            )

        # 4) System One Decision Endpoints (/v1/systemone & /v1/decisions)
        if path in ("/v1/systemone", "/v1/decisions"):
            body = json.loads(req.content.decode("utf-8"))
            st = body.get("state") or {}
            questions = body.get("questions") or {}
            inp = str(st.get("input", ""))
            actual = str(st.get("actual_output") or st.get("current_output") or "")

            # Simulate Edge WAF 403 once when raw fenced bash block is sent to /v1/decisions
            if path == "/v1/decisions" and "```bash" in actual:
                return httpx.Response(
                    403,
                    headers={"Content-Type": "text/html"},
                    text="<html><body><h1>403 Forbidden - Cloud Edge WAF</h1></body></html>",
                )

            # 4-way Semantic Regression prompt
            if "semantic_regression_v1__diff" in questions:
                if "灵活就业社保补贴" in inp or "连续缴社保满几个月" in inp:
                    probs = {"SAME": 0.04, "FACT_DIFFERS": 0.88, "ACTION_DIFFERS": 0.05, "SPECIFICITY_DIFFERS": 0.03}
                elif "苏州买了自住商品房" in inp or "仅销售预包装食品" in inp:
                    probs = {"SAME": 0.05, "FACT_DIFFERS": 0.07, "ACTION_DIFFERS": 0.82, "SPECIFICITY_DIFFERS": 0.06}
                elif "新生儿出生后多久" in inp:
                    probs = {"SAME": 0.11, "FACT_DIFFERS": 0.03, "ACTION_DIFFERS": 0.05, "SPECIFICITY_DIFFERS": 0.81}
                else:
                    probs = {"SAME": 0.93, "FACT_DIFFERS": 0.02, "ACTION_DIFFERS": 0.02, "SPECIFICITY_DIFFERS": 0.03}
                return httpx.Response(
                    200,
                    headers={"X-Request-ID": f"s1_reg_{sha256_hex(inp)[:10]}"},
                    json={
                        "answers": {"semantic_regression_v1__diff": {"probabilities": probs}},
                        "usage": {"input_tokens": 260, "output_tokens": 14},
                    },
                )

            # Graduated probability distribution so slider calibration & Youden's J optimization shine!
            p_good = 0.91
            if "最高额度是多少" in inp:
                p_good = 0.95
            elif "苏州买了自住商品房" in inp:
                p_good = 0.64 if "线下柜台" in actual else 0.91
            elif "新生儿出生后多久" in inp:
                p_good = 0.83 if "出生一件事" not in actual else 0.93
            elif "灵活就业社保补贴" in inp:
                p_good = 0.38 if "一律最长只能申领 3 年" in actual else 0.86
            elif "仅销售预包装食品" in inp:
                p_good = 0.31 if "需要先申请《食品经营许可证》" in actual else 0.72
            elif "8万元创业启动现金红包" in inp:
                p_good = 0.56
            elif "连续缴社保满几个月" in inp:
                p_good = 0.42 if "满 6 个月" in actual else 0.90
            elif "边缘模糊路由" in inp:
                p_good = 0.61

            # Slight calibration shift on cloud model `typesafe/jev-1.14.0` to illustrate cross-model scale difference
            if "jev-1.14" in str(body.get("model", "")):
                p_good = min(0.98, round(p_good + 0.03, 4))

            answers: dict[str, Any] = {}
            for idx, (q_key, q_spec) in enumerate(questions.items()):
                # Tiny per-question spread for realistic ledger inspection
                q_p = max(0.08, min(0.98, round(p_good - (idx % 3) * 0.02, 4)))
                qt = q_spec["type"]
                if qt == "noul":
                    answers[q_key] = {"noul": q_p}
                elif qt == "score":
                    k = len(q_spec["criteria"])
                    mid_p = round((1.0 - q_p) * 0.45, 6)
                    low_p = round(1.0 - q_p - mid_p, 6)
                    if k == 3:
                        probs_arr = [low_p, mid_p, q_p]
                    else:
                        probs_arr = [low_p] + [0.0] * max(0, k - 3) + [mid_p, q_p]
                    answers[q_key] = {"probabilities": probs_arr}
                elif qt == "choice":
                    opts = list(q_spec["criteria"].keys())
                    good_idx = max(0, len(opts) - 2)
                    bad_idx = 0
                    pm: dict[str, float] = {o: 0.0 for o in opts}
                    pm[opts[good_idx]] = q_p
                    pm[opts[bad_idx]] = round(1.0 - q_p, 6)
                    answers[q_key] = {"probabilities": pm}

            return httpx.Response(
                200,
                headers={"X-Request-ID": f"s1_batch_{sha256_hex(inp + actual)[:12]}"},
                json={
                    "answers": answers,
                    "usage": {"input_tokens": 380 + 45 * len(questions), "output_tokens": 18},
                },
            )

        raise RuntimeError(f"Unhandled demo mock URL: {req.url}")

    return httpx.MockTransport(handler)


async def seed_demo_showcase(*, settings: Settings | None = None, force: bool = False) -> dict[str, Any]:
    cfg = settings or get_settings()
    engine = get_engine(cfg)
    init_db(engine, seed=True, settings=cfg)
    session_factory = build_session_factory(engine)

    with session_factory() as db:
        existing_ds = db.scalar(
            select(Dataset).where(
                Dataset.dataset_code == "gov_cs_e2e_golden",
                Dataset.is_deleted == 0,
            )
        )
        if existing_ds is not None and not force:
            return {"status": "already_seeded", "e2e_dataset_id": existing_ds.id}

        admin = db.scalar(
            select(AdminUser).where(AdminUser.username == cfg.default_admin_username)
        )
        assert admin is not None, "Admin user missing after init_db"

        # Enrich provider health statuses & masked credential preview
        now = utcnow_naive()
        for p in db.scalars(select(DecisionProvider).where(DecisionProvider.is_deleted == 0)).all():
            p.last_checked_at = now - timedelta(minutes=8)
            if p.provider_code == "ollama_local_tev1":
                p.last_health_ok = 1
            elif p.provider_code == "emrank_decisions_relay":
                p.last_health_ok = 1
                demo_secret = "sk-emrank-demo-showcase-9a2f"
                p.api_key_cipher = encrypt_secret(demo_secret, cfg.master_encryption_key)
                p.api_key_masked = mask_secret(demo_secret)
            elif p.provider_code == "typesafe_official":
                p.last_health_ok = 0

        # Seed 2 showcase API Keys (1 ACTIVE, 1 REVOKED)
        for key_name, raw_key, rpm, status_val, used_delta in [
            ("ci-nightly-regression-bot", "evk_live_demo_ci_nightly_2026_active99", 300, "ACTIVE", 15),
            ("legacy-import-script-2025", "evk_live_demo_legacy_importer_revoked01", 60, "REVOKED", None),
        ]:
            k_hash = sha256_hex(raw_key)
            if db.scalar(select(ApiKey).where(ApiKey.key_sha256 == k_hash)) is None:
                db.add(
                    ApiKey(
                        key_name=key_name,
                        key_prefix=raw_key[:13],
                        key_sha256=k_hash,
                        rate_limit_rpm=rpm,
                        status=status_val,
                        expires_at=now + timedelta(days=90),
                        last_used_at=(now - timedelta(minutes=used_delta)) if used_delta else None,
                        created_by=admin.username,
                    )
                )
        db.commit()

    # Set environment tokens referenced by seeded adapters/providers during demo run
    os.environ.setdefault("EVAL_JAVA_BEARER_TOKEN", "demo-java-e2e-bearer-2026")
    os.environ.setdefault("ROUTING_EVAL_HARNESS_TOKEN", "demo-py-harness-token-2026")
    os.environ.setdefault("EMRANK_API_KEY", "sk-emrank-demo-showcase-9a2f")

    state: dict[str, Any] = {"sut_version": "v2.3.0"}
    mock_transport = _build_demo_mock_transport(state)

    app = create_app(cfg)
    app.dependency_overrides[get_desensitize_http_transport] = lambda: mock_transport
    app.dependency_overrides[get_sut_http_transport] = lambda: mock_transport
    app.dependency_overrides[get_provider_http_transport] = lambda: mock_transport

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://demo.local",
    ) as client:
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={
                "username": cfg.default_admin_username,
                "password": cfg.default_admin_password,
            },
        )
        assert login_resp.status_code == 200, login_resp.text
        token = login_resp.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1) Register HTTP_SSE SUT Adapter
        await client.post(
            "/api/v1/adapters",
            headers=headers,
            json={
                "adapter_code": "gov_e2e_routing_sse",
                "adapter_name": "政务智能客服-智能路由 SSE 流式入口 (含 TTFT 采集)",
                "eval_level": "E2E",
                "adapter_type": "HTTP_SSE",
                "endpoint_url": "http://127.0.0.1:8080/ai/invoke-sse",
                "auth_config_json": {
                    "header": "Authorization",
                    "prefix": "Bearer ",
                    "env_name": "EVAL_JAVA_BEARER_TOKEN",
                },
                "request_mapping_json": {
                    "body_template": {"responseMode": "streaming", "routeMode": "auto"},
                    "field_map": {
                        "query": "$.input_text",
                        "selectedRegion": "$.sut_input_json.selected_region",
                    },
                },
                "response_mapping_json": {
                    "chunk_text_path": "$.answer",
                    "actual_references": "$.references[*].content",
                    "actual_structured": {"actual_agent_id": "$.finalAgentId"},
                },
                "description": "对接政务客服 SSE 流式网关，自动统计首字延迟 TTFT 与完整流式耗时",
            },
        )

        # 2) Evolve rag_faithfulness_s1 -> v2 & add answer_relevancy_s1 + citation_latency_sla
        await client.post(
            "/api/v1/dimensions",
            headers=headers,
            json={
                "dimension_code": "rag_faithfulness_s1",
                "dimension_name": "知识引用忠实度与零编造 (System One v2·含时效对齐)",
                "category": "GROUNDING",
                "target_turn_mode": "SINGLE_TURN",
                "evaluator_kind": "SYSTEM_ONE",
                "state_fields_json": ["input", "actual_output", "references"],
                "questions_json": [
                    {
                        "key": "grounded_in_refs",
                        "type": "score",
                        "weight": 2.0,
                        "instructions": "actual_output 中给出的实质性办理结论、条件与流程在多大程度上由 references 支撑？",
                        "criteria": [
                            "大量关键结论在 references 中毫无依据或与 references 矛盾",
                            "部分结论有依据，但夹杂了 references 未提及的具体条件或流程",
                            "绝大部分内容有依据，仅有不影响结论的常识性衔接表述",
                            "所有实质性结论、条件与操作细节均完全忠实于 references",
                        ],
                    },
                    {
                        "key": "honest_on_missing",
                        "type": "choice",
                        "weight": 1.5,
                        "instructions": "对于用户提问中涉及但 references 未提供的信息，actual_output 如何处理？",
                        "criteria": {
                            "fabricated_as_fact": "将 references 中不存在的具体规定或材料当作确定事实写出",
                            "vague_guess": "做了模棱两可的猜测且未提示需核实",
                            "honest_boundary": "明确说明暂未查到或引导通过官方咨询渠道核实",
                            "nothing_missing": "references 已完整覆盖用户所问内容，不存在缺失项",
                        },
                        "option_scores": {
                            "fabricated_as_fact": 0.0,
                            "vague_guess": 0.4,
                            "honest_boundary": 1.0,
                            "nothing_missing": None,
                        },
                    },
                    {
                        "key": "policy_timeliness",
                        "type": "noul",
                        "weight": 1.0,
                        "instructions": "actual_output 未将已废止年份或与 references 时效冲突的口径当作现行有效政策输出。",
                    },
                ],
                "default_threshold": 0.75,
                "strict_mode": False,
                "default_weight": 2.0,
                "status": "ACTIVE",
                "description": "v2 升级版：新增政策时效对齐原子判题，与回答相关性共享三元组上下文实现单次合批调用",
            },
        )

        await client.post(
            "/api/v1/dimensions",
            headers=headers,
            json={
                "dimension_code": "answer_relevancy_s1",
                "dimension_name": "诉求响应相关性与条理性 (System One)",
                "category": "RELEVANCY",
                "target_turn_mode": "SINGLE_TURN",
                "evaluator_kind": "SYSTEM_ONE",
                "state_fields_json": ["input", "actual_output", "references"],
                "questions_json": [
                    {
                        "key": "direct_response",
                        "type": "noul",
                        "weight": 1.5,
                        "instructions": "actual_output 开门见山回应了 input 中的核心办理诉求。",
                    },
                    {
                        "key": "no_irrelevant_padding",
                        "type": "noul",
                        "weight": 1.0,
                        "instructions": "actual_output 未堆砌与 input 所询事项无关的冗余套话。",
                    },
                    {
                        "key": "clear_structure",
                        "type": "noul",
                        "weight": 1.0,
                        "instructions": "actual_output 条件与步骤层次清晰、便于市民直接照章办理。",
                    },
                ],
                "default_threshold": 0.75,
                "strict_mode": False,
                "default_weight": 1.5,
                "status": "ACTIVE",
                "description": "与 rag_faithfulness_s1@v2 共享 (input, actual_output, references) 状态签名，自动合并为单次请求",
            },
        )

        await client.post(
            "/api/v1/dimensions",
            headers=headers,
            json={
                "dimension_code": "citation_latency_sla",
                "dimension_name": "政务流式首字与总耗时 SLA 门禁",
                "category": "PERFORMANCE",
                "target_turn_mode": "BOTH",
                "evaluator_kind": "DETERMINISTIC",
                "deterministic_rule_json": {
                    "rule_type": "latency_sla",
                    "max_latency_ms": 2500,
                    "max_ttft_ms": 800,
                },
                "default_threshold": 1.0,
                "strict_mode": False,
                "default_weight": 1.0,
                "status": "ACTIVE",
                "description": "零成本确定性校验流式首字响应 TTFT <= 800ms 且全链路总耗时 <= 2500ms",
            },
        )

        # 3) Create Flagship SSE Batch Demo Suite
        suite_resp = await client.post(
            "/api/v1/suites",
            headers=headers,
            json={
                "suite_code": "suite_gov_sse_batch_demo_v1",
                "suite_name": "政务智能客服 SSE 全链路合批验收套件 (v1)",
                "eval_level": "E2E",
                "default_adapter_id": None,
                "default_provider_id": None,
                "dimensions_json": [
                    {"dimension_code": "route_top1_match", "version": 1, "weight": 1.5, "threshold": 1.0, "is_blocker": True},
                    {"dimension_code": "numeric_fact_grounding", "version": 1, "weight": 1.5, "threshold": 1.0, "is_blocker": True},
                    {"dimension_code": "rag_faithfulness_s1", "version": 2, "weight": 2.0, "threshold": 0.75, "is_blocker": True},
                    {"dimension_code": "answer_relevancy_s1", "version": 1, "weight": 1.5, "threshold": 0.75, "is_blocker": False},
                    {"dimension_code": "policy_and_action_accuracy_s1", "version": 1, "weight": 2.5, "threshold": 0.75, "is_blocker": True},
                    {"dimension_code": "citation_latency_sla", "version": 1, "weight": 1.0, "threshold": 1.0, "is_blocker": False},
                ],
                "pass_policy": "ALL_BLOCKERS_AND_WEIGHTED",
                "suite_pass_threshold": 0.76,
                "confidence_review_floor": 0.50,
                "enable_semantic_regression": True,
                "status": "ACTIVE",
                "description": "融合 3 个确定性规则维度 + 3 个 System One 概率维度（其中 6 题同上下文单次合批），用于案例演示",
            },
        )
        assert suite_resp.status_code == 200, suite_resp.text

        # 4) Create E2E Golden Dataset & Seed 16 Cases (6 DEV + 6 HOLDOUT + 4 PENDING_REVIEW)
        ds_e2e_resp = await client.post(
            "/api/v1/datasets",
            headers=headers,
            json={
                "dataset_code": "gov_cs_e2e_golden",
                "version_tag": "v2026.11",
                "dataset_name": "政务智能客服黄金验收与校准集 (常州高频 16 题)",
                "eval_level": "E2E",
                "desensitize_policy_snapshot": "gov_eval_corpus:v2.3",
                "status": "PUBLISHED",
                "description": "覆盖公积金、医保、人社、市监、不动产、民政 6 大条线，含 DEV 调参集、HOLDOUT 盲测集与待校准高不确定度样本",
            },
        )
        assert ds_e2e_resp.status_code == 200, ds_e2e_resp.text
        e2e_ds_id = ds_e2e_resp.json()["data"]["id"]

        # Insert E2E cases preserving exact splits, review_status, pii_masked, and uncertainty_score
        from app.ingestion.service import recompute_dataset_counts

        with session_factory() as db:
            for item in E2E_DEMO_CASES:
                payload = dict(item)
                is_approved = payload.get("review_status") == "APPROVED"
                db.add(
                    EvalCase(
                        dataset_id=e2e_ds_id,
                        reviewed_by=admin.username if is_approved else None,
                        reviewed_at=(utcnow_naive() - timedelta(hours=2)) if is_approved else None,
                        **payload,
                    )
                )
            db.flush()
            recompute_dataset_counts(db, e2e_ds_id)
            db.commit()

        # 5) Create Component Snapshot Dataset, Insert 6 Cases & Record Frozen Snapshots
        ds_comp_resp = await client.post(
            "/api/v1/datasets",
            headers=headers,
            json={
                "dataset_code": "gov_routing_comp_snapshots",
                "version_tag": "v1.2",
                "dataset_name": "智能路由与汇总组件冻结快照集",
                "eval_level": "COMPONENT",
                "desensitize_policy_snapshot": "gov_eval_corpus:v2.3",
                "status": "PUBLISHED",
                "description": "已录制 Python FastRouter / SupervisorRouter 冻结入参快照，用于脱离外网数据库的毫秒级确定性回放消融",
            },
        )
        assert ds_comp_resp.status_code == 200, ds_comp_resp.text
        comp_ds_id = ds_comp_resp.json()["data"]["id"]

        with session_factory() as db:
            for item in COMPONENT_DEMO_CASES:
                db.add(
                    EvalCase(
                        dataset_id=comp_ds_id,
                        reviewed_by=admin.username,
                        reviewed_at=utcnow_naive() - timedelta(hours=1),
                        **item,
                    )
                )
            db.flush()
            recompute_dataset_counts(db, comp_ds_id)
            db.commit()

        rec_resp = await client.post(
            f"/api/v1/datasets/{comp_ds_id}/record-component-snapshots",
            headers=headers,
            json={
                "adapter_code": "gov_comp_routing_harness",
                "only_missing": True,
            },
        )
        assert rec_resp.status_code == 200, rec_resp.text

        # 6) Execute 5 Showcase Eval Runs synchronously through the real kernel!
        # Run 1: DEV Baseline v2.3.0
        state["sut_version"] = "v2.3.0"
        r1 = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "run_name": "政务客服 v2.3.0 基线评测 (DEV 调参集)",
                "suite_code": "suite_gov_sse_batch_demo_v1",
                "dataset_id": e2e_ds_id,
                "dataset_split_filter": "DEV",
                "sut_adapter_code": "gov_e2e_routing_sse",
                "provider_code": "ollama_local_tev1",
                "sut_fingerprint": {"git_sha": "7b21e09", "prompt_ver": "router_v2.3", "kb_snapshot": "2026-10"},
                "run_sync": True,
            },
        )
        assert r1.status_code == 200, r1.text
        run1_id = r1.json()["data"]["id"]
        run1_no = r1.json()["data"]["run_no"]

        # Run 2: DEV Candidate v2.4.0-rc1 (linked to Baseline Run 1 -> 4-way semantic regression!)
        state["sut_version"] = "v2.4.0"
        r2 = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "run_name": "政务客服 v2.4.0-rc1 检索与提示词重构回归 (DEV)",
                "suite_code": "suite_gov_sse_batch_demo_v1",
                "dataset_id": e2e_ds_id,
                "dataset_split_filter": "DEV",
                "sut_adapter_code": "gov_e2e_routing_sse",
                "provider_code": "ollama_local_tev1",
                "baseline_run_id": run1_id,
                "sut_fingerprint": {"git_sha": "4cd9137", "prompt_ver": "router_v2.4_rc1", "kb_snapshot": "2026-11"},
                "run_sync": True,
            },
        )
        assert r2.status_code == 200, r2.text
        run2_no = r2.json()["data"]["run_no"]

        # Run 3: HOLDOUT Baseline v2.3.0 (Local tev1:0.8b)
        state["sut_version"] = "v2.3.0"
        r3 = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "run_name": "政务客服 v2.3.0 盲测基线 (HOLDOUT · 本地模型)",
                "suite_code": "suite_gov_sse_batch_demo_v1",
                "dataset_id": e2e_ds_id,
                "dataset_split_filter": "HOLDOUT",
                "sut_adapter_code": "gov_e2e_routing_sse",
                "provider_code": "ollama_local_tev1",
                "sut_fingerprint": {"git_sha": "7b21e09", "prompt_ver": "router_v2.3", "release_stage": "baseline"},
                "run_sync": True,
            },
        )
        assert r3.status_code == 200, r3.text
        run3_id = r3.json()["data"]["id"]
        run3_no = r3.json()["data"]["run_no"]

        # Run 4: HOLDOUT Release Gate v2.4.0 on Cloud EMRank (demonstrates HOLDOUT lock + cross_model_warning + WAF retry + cost!)
        state["sut_version"] = "v2.4.0_emrank_waf"
        r4 = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "run_name": "政务客服 v2.4.0 发布门禁盲测验收 (HOLDOUT · EMRank 云端)",
                "suite_code": "suite_gov_sse_batch_demo_v1",
                "dataset_id": e2e_ds_id,
                "dataset_split_filter": "HOLDOUT",
                "sut_adapter_code": "gov_e2e_routing_sse",
                "provider_code": "emrank_decisions_relay",
                "baseline_run_id": run3_id,
                "sut_fingerprint": {"git_sha": "8601fbc", "prompt_ver": "router_v2.4_ga", "release_gate": True},
                "run_sync": True,
            },
        )
        assert r4.status_code == 200, r4.text
        run4_no = r4.json()["data"]["run_no"]

        # Run 5: Component Harness Zero-Network Snapshot Replay Ablation
        r5 = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "run_name": "FastRouter 温度 T=0.0 组件快照零网络回放消融",
                "suite_code": "suite_gov_comp_routing_v1",
                "dataset_id": comp_ds_id,
                "dataset_split_filter": "ALL",
                "sut_adapter_code": "gov_comp_routing_harness",
                "provider_code": "ollama_local_tev1",
                "sut_fingerprint": {"component": "FastRouter", "temperature": 0.0, "snapshot_tag": "v1.2"},
                "run_overrides": {"temperature": 0.0},
                "run_sync": True,
            },
        )
        assert r5.status_code == 200, r5.text
        run5_no = r5.json()["data"]["run_no"]

    return {
        "status": "seeded",
        "e2e_dataset_id": e2e_ds_id,
        "component_dataset_id": comp_ds_id,
        "runs": [run1_no, run2_no, run3_no, run4_no, run5_no],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed AI Evaluation Center showcase mock data.")
    parser.add_argument("--force", action="store_true", help="Run even if demo dataset exists.")
    args = parser.parse_args()
    summary = asyncio.run(seed_demo_showcase(force=args.force))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
