"""Ecosystem Map — สารานุกรมอุตสาหกรรมเชิงลึกระดับ "circle of competence" แบบ Warren Buffett

โจทย์: ก่อนถือหุ้นตัวไหนยาว ต้องเข้าใจ "ระบบนิเวศ" ของสินค้า/อุตสาหกรรมนั้นให้ลึกพอจะไม่ตกใจ
ขายตอนราคาผันผวน — ต่างจาก industry_peers.py (เทียบ P/E กลุ่มเชิงสถิติ) และ macro_business.py
(วิเคราะห์ต่อ "รายหุ้น" จากเอกสาร 10-K) ตรงที่โมดูลนี้เขียนเป็น "บท" ต่อ "ธีมสินค้า/อุตสาหกรรม"
เดียว ใช้ร่วมกันได้ทุกหุ้นที่อยู่ใน ecosystem เดียวกัน โดยอธิบายผ่านบริษัทจริง 2-4 รายต่อ
component พร้อม framework วิเคราะห์ (Five Forces, profit pool, pre-mortem) ยึดตามที่คุยออกแบบไว้

การ generate: เขียนทีเดียวจบไม่ได้ (เนื้อหาลึกเกิน max_tokens ต่อคำตอบ) — แบ่งเป็นหลาย LLM call
ต่อ 1 theme: 1 call ร่างภาพรวม+รายชื่อ component แล้ว "1 call ต่อ component" ลงลึกแยกกัน
ผลลัพธ์ cache ถาวรลงดิสก์ (evergreen — โครงสร้างอุตสาหกรรมไม่เปลี่ยนรายวัน) เจน on-demand ตอน
ผู้ใช้เปิดดู theme นั้นครั้งแรก ไม่ pre-generate ทั้ง 85 theme ล่วงหน้า.

ชื่อบริษัท/ticker ที่ LLMยกมา จะถูก "ground" กับฐานข้อมูลจริงของแอป (sp500_constituents +
แคชสแกน P/E อุตสาหกรรม ผ่าน industry_peers) — ตัวไหน match ได้ถือว่า grounded=True และมี
ข้อมูล P/E กลุ่มจริงแนบให้ ตัวไหนไม่ match ได้ (ต่างชาติ/ไม่อยู่ในดัชนี) ยังคงชื่อไว้อธิบาย
ได้แต่ไม่มีลิงก์วิเคราะห์ต่อ.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from app import industry_peers, llm
from app.config import get_settings

_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "financials"
_TTL = 180 * 24 * 3600  # เนื้อหาระดับโครงสร้างอุตสาหกรรม evergreen — cache ยาว 180 วัน

# ── Theme registry — 85 ธีมหลัก ครอบคลุมทั้ง 11 GICS sector + ธีมข้าม sector ────────────
THEMES: list[dict] = [
    # Information Technology (12)
    {"slug": "smartphone-mobile-devices", "name_th": "สมาร์ทโฟนและอุปกรณ์พกพา", "sector": "Information Technology"},
    {"slug": "pc-laptop-peripherals", "name_th": "พีซี/โน้ตบุ๊กและอุปกรณ์ต่อพ่วง", "sector": "Information Technology"},
    {"slug": "semiconductor-design-fabless", "name_th": "ออกแบบชิป (Fabless)", "sector": "Information Technology"},
    {"slug": "semiconductor-foundry", "name_th": "โรงหล่อผลิตชิป (Foundry)", "sector": "Information Technology"},
    {"slug": "semiconductor-equipment", "name_th": "เครื่องจักรผลิตชิป", "sector": "Information Technology"},
    {"slug": "cloud-infrastructure", "name_th": "โครงสร้างพื้นฐานคลาวด์", "sector": "Information Technology"},
    {"slug": "enterprise-software-saas", "name_th": "ซอฟต์แวร์องค์กร/SaaS", "sector": "Information Technology"},
    {"slug": "cybersecurity", "name_th": "ความปลอดภัยไซเบอร์", "sector": "Information Technology"},
    {"slug": "ai-ml-infrastructure", "name_th": "โครงสร้างพื้นฐาน AI/ML", "sector": "Information Technology"},
    {"slug": "data-center-networking", "name_th": "ดาต้าเซ็นเตอร์และเครือข่าย", "sector": "Information Technology"},
    {"slug": "payments-fintech-software", "name_th": "ซอฟต์แวร์ชำระเงิน/ฟินเทค", "sector": "Information Technology"},
    {"slug": "it-services-consulting", "name_th": "บริการ/ที่ปรึกษาไอที", "sector": "Information Technology"},
    # Communication Services (6)
    {"slug": "streaming-media", "name_th": "สตรีมมิ่งสื่อบันเทิง", "sector": "Communication Services"},
    {"slug": "social-media-platforms", "name_th": "แพลตฟอร์มโซเชียลมีเดีย", "sector": "Communication Services"},
    {"slug": "telecom-network-infrastructure", "name_th": "โครงข่ายโทรคมนาคม (5G/ไฟเบอร์)", "sector": "Communication Services"},
    {"slug": "video-gaming-esports", "name_th": "วิดีโอเกม/อีสปอร์ต", "sector": "Communication Services"},
    {"slug": "digital-advertising", "name_th": "โฆษณาดิจิทัล", "sector": "Communication Services"},
    {"slug": "publishing-media-content", "name_th": "สื่อสิ่งพิมพ์/คอนเทนต์", "sector": "Communication Services"},
    # Consumer Discretionary (10)
    {"slug": "electric-vehicles", "name_th": "ยานยนต์ไฟฟ้า", "sector": "Consumer Discretionary"},
    {"slug": "traditional-auto-parts", "name_th": "ยานยนต์สันดาปและชิ้นส่วน", "sector": "Consumer Discretionary"},
    {"slug": "ecommerce-online-retail", "name_th": "อีคอมเมิร์ซ", "sector": "Consumer Discretionary"},
    {"slug": "apparel-footwear-brands", "name_th": "แบรนด์เสื้อผ้า/รองเท้า", "sector": "Consumer Discretionary"},
    {"slug": "restaurants-qsr", "name_th": "ร้านอาหาร/ฟาสต์ฟู้ด", "sector": "Consumer Discretionary"},
    {"slug": "travel-airlines-hospitality", "name_th": "ท่องเที่ยว/สายการบิน/โรงแรม", "sector": "Consumer Discretionary"},
    {"slug": "luxury-goods", "name_th": "สินค้าหรู", "sector": "Consumer Discretionary"},
    {"slug": "home-improvement-retail", "name_th": "ค้าปลีกวัสดุ/ตกแต่งบ้าน", "sector": "Consumer Discretionary"},
    {"slug": "homebuilding", "name_th": "พัฒนาอสังหาที่อยู่อาศัย", "sector": "Consumer Discretionary"},
    {"slug": "specialty-bigbox-retail", "name_th": "ค้าปลีกเฉพาะทาง/บิ๊กบ็อกซ์", "sector": "Consumer Discretionary"},
    # Consumer Staples (5)
    {"slug": "non-alcoholic-beverages", "name_th": "เครื่องดื่มไม่มีแอลกอฮอล์", "sector": "Consumer Staples"},
    {"slug": "alcoholic-beverages", "name_th": "เครื่องดื่มแอลกอฮอล์", "sector": "Consumer Staples"},
    {"slug": "packaged-food", "name_th": "อาหารบรรจุสำเร็จ", "sector": "Consumer Staples"},
    {"slug": "household-personal-care", "name_th": "สินค้าอุปโภค/ของใช้ส่วนตัว", "sector": "Consumer Staples"},
    {"slug": "tobacco", "name_th": "ยาสูบ", "sector": "Consumer Staples"},
    # Health Care (8)
    {"slug": "large-cap-pharma", "name_th": "ยาบริษัทใหญ่", "sector": "Health Care"},
    {"slug": "biotechnology", "name_th": "เทคโนโลยีชีวภาพ", "sector": "Health Care"},
    {"slug": "medical-devices", "name_th": "เครื่องมือแพทย์", "sector": "Health Care"},
    {"slug": "healthcare-services-hospitals", "name_th": "บริการสุขภาพ/โรงพยาบาล", "sector": "Health Care"},
    {"slug": "health-insurance-managed-care", "name_th": "ประกันสุขภาพ", "sector": "Health Care"},
    {"slug": "diagnostics-life-science-tools", "name_th": "เครื่องมือวินิจฉัย/วิทยาศาสตร์ชีวภาพ", "sector": "Health Care"},
    {"slug": "generic-specialty-pharma", "name_th": "ยาสามัญ/ยาเฉพาะทาง", "sector": "Health Care"},
    {"slug": "digital-health", "name_th": "สุขภาพดิจิทัล", "sector": "Health Care"},
    # Financials (7)
    {"slug": "commercial-retail-banking", "name_th": "ธนาคารพาณิชย์/รายย่อย", "sector": "Financials"},
    {"slug": "investment-banking-capital-markets", "name_th": "วาณิชธนกิจ/ตลาดทุน", "sector": "Financials"},
    {"slug": "insurance-pc", "name_th": "ประกันวินาศภัย", "sector": "Financials"},
    {"slug": "insurance-life-health", "name_th": "ประกันชีวิต/สุขภาพ", "sector": "Financials"},
    {"slug": "asset-wealth-management", "name_th": "บริหารสินทรัพย์/ความมั่งคั่ง", "sector": "Financials"},
    {"slug": "consumer-payments-fintech", "name_th": "ฟินเทค/ชำระเงินผู้บริโภค", "sector": "Financials"},
    {"slug": "exchanges-market-infrastructure", "name_th": "ตลาดหลักทรัพย์/โครงสร้างพื้นฐานตลาดทุน", "sector": "Financials"},
    # Industrials (9)
    {"slug": "aerospace-defense", "name_th": "การบิน/อวกาศและกลาโหม", "sector": "Industrials"},
    {"slug": "freight-logistics-shipping", "name_th": "ขนส่ง/โลจิสติกส์/เดินเรือ", "sector": "Industrials"},
    {"slug": "industrial-automation-robotics", "name_th": "ระบบอัตโนมัติ/หุ่นยนต์อุตสาหกรรม", "sector": "Industrials"},
    {"slug": "construction-engineering", "name_th": "ก่อสร้าง/วิศวกรรม", "sector": "Industrials"},
    {"slug": "electrical-equipment-machinery", "name_th": "อุปกรณ์ไฟฟ้า/เครื่องจักร", "sector": "Industrials"},
    {"slug": "waste-management", "name_th": "จัดการของเสีย/สิ่งแวดล้อม", "sector": "Industrials"},
    {"slug": "agricultural-machinery", "name_th": "เครื่องจักรการเกษตร", "sector": "Industrials"},
    {"slug": "railroads", "name_th": "ระบบราง", "sector": "Industrials"},
    {"slug": "airlines-air-cargo", "name_th": "สายการบิน/ขนส่งสินค้าทางอากาศ", "sector": "Industrials"},
    # Energy (6)
    {"slug": "oil-gas-upstream", "name_th": "น้ำมัน/ก๊าซ ต้นน้ำ", "sector": "Energy"},
    {"slug": "oil-gas-midstream", "name_th": "น้ำมัน/ก๊าซ กลางน้ำ (ท่อขนส่ง)", "sector": "Energy"},
    {"slug": "oil-gas-downstream-refining", "name_th": "น้ำมัน/ก๊าซ ปลายน้ำ (โรงกลั่น)", "sector": "Energy"},
    {"slug": "solar-energy", "name_th": "พลังงานแสงอาทิตย์", "sector": "Energy"},
    {"slug": "wind-energy", "name_th": "พลังงานลม", "sector": "Energy"},
    {"slug": "oilfield-services-equipment", "name_th": "บริการ/อุปกรณ์แหล่งน้ำมัน", "sector": "Energy"},
    # Materials (6)
    {"slug": "base-metals-mining", "name_th": "เหมืองแร่โลหะพื้นฐาน", "sector": "Materials"},
    {"slug": "precious-metals-mining", "name_th": "เหมืองแร่โลหะมีค่า", "sector": "Materials"},
    {"slug": "battery-ev-materials", "name_th": "วัตถุดิบแบตเตอรี่/EV (ลิเทียม/แรร์เอิร์ธ)", "sector": "Materials"},
    {"slug": "commodity-chemicals", "name_th": "เคมีภัณฑ์พื้นฐาน", "sector": "Materials"},
    {"slug": "specialty-chemicals", "name_th": "เคมีภัณฑ์พิเศษ", "sector": "Materials"},
    {"slug": "packaging-materials", "name_th": "วัสดุบรรจุภัณฑ์", "sector": "Materials"},
    # Utilities (4)
    {"slug": "electric-utilities", "name_th": "สาธารณูปโภคไฟฟ้า", "sector": "Utilities"},
    {"slug": "renewable-utility-power", "name_th": "โรงไฟฟ้าพลังงานหมุนเวียนขนาดใหญ่", "sector": "Utilities"},
    {"slug": "water-utilities", "name_th": "สาธารณูปโภคน้ำประปา", "sector": "Utilities"},
    {"slug": "natural-gas-utilities", "name_th": "สาธารณูปโภคก๊าซธรรมชาติ", "sector": "Utilities"},
    # Real Estate (5)
    {"slug": "data-center-reits", "name_th": "REIT ดาต้าเซ็นเตอร์", "sector": "Real Estate"},
    {"slug": "residential-reits", "name_th": "REIT ที่อยู่อาศัย", "sector": "Real Estate"},
    {"slug": "commercial-office-reits", "name_th": "REIT อาคารสำนักงาน", "sector": "Real Estate"},
    {"slug": "industrial-logistics-reits", "name_th": "REIT คลังสินค้า/โลจิสติกส์", "sector": "Real Estate"},
    {"slug": "retail-reits", "name_th": "REIT ค้าปลีก", "sector": "Real Estate"},
    # ข้ามหลาย Sector (7)
    {"slug": "energy-storage-battery-tech", "name_th": "เทคโนโลยีกักเก็บพลังงาน/แบตเตอรี่", "sector": "Cross-Sector"},
    {"slug": "ev-charging-infrastructure", "name_th": "โครงสร้างพื้นฐานชาร์จ EV", "sector": "Cross-Sector"},
    {"slug": "space-economy", "name_th": "เศรษฐกิจอวกาศ", "sector": "Cross-Sector"},
    {"slug": "water-scarcity-desalination", "name_th": "วิกฤตน้ำ/การแยกเกลือ", "sector": "Cross-Sector"},
    {"slug": "genomics-precision-medicine", "name_th": "จีโนมิกส์/การแพทย์แม่นยำ", "sector": "Cross-Sector"},
    {"slug": "glp1-obesity-drug-ecosystem", "name_th": "ระบบนิเวศยา GLP-1/โรคอ้วน", "sector": "Cross-Sector"},
    {"slug": "rare-earth-critical-minerals", "name_th": "แรร์เอิร์ธ/แร่ธาตุวิกฤต", "sector": "Cross-Sector"},
]
_THEME_BY_SLUG = {t["slug"]: t for t in THEMES}

SECTOR_ORDER = [
    "Information Technology", "Communication Services", "Consumer Discretionary",
    "Consumer Staples", "Health Care", "Financials", "Industrials", "Energy",
    "Materials", "Utilities", "Real Estate", "Cross-Sector",
]

_LANG_RULE = (
    "กฎภาษา: ทุกข้อความต้องเป็นภาษาไทยอ่านรู้เรื่อง ถ้าต้องอ้างชื่อเฉพาะภาษาอังกฤษ "
    "(ชื่อบริษัท/ศัพท์เทคนิค/ตัวย่อ) ให้คงไว้ได้แต่ประโยคอธิบายรอบข้างต้องเป็นไทย "
    "ข้อบังคับ: ตอบเป็น JSON เท่านั้น ห้ามมีข้อความนอก JSON ห้ามใช้ ``` ครอบ"
)

_OVERVIEW_SYSTEM = f"""คุณคือนักวิเคราะห์อุตสาหกรรมระดับ sell-side เขียน "industry primer" เชิงลึก
เป้าหมายเดียว: สร้าง "circle of competence" แบบ Warren Buffett ให้นักลงทุนเข้าใจอุตสาหกรรมนี้
ลึกพอจะถือหุ้นในนั้นได้ยาวหลายปีโดยไม่ตกใจขายตอนราคาผันผวน — ไม่ใช่แค่ให้ข้อมูล แต่ต้องสร้าง
"ภูมิคุ้มกันทางความเข้าใจ" ต่อความผันผวนปกติของอุตสาหกรรมนี้ด้วย

หลักการเขียน:
- plain_narrative ต้องเล่าแบบไม่มีศัพท์ก่อน เหมือนเล่าให้คนไม่รู้จักอุตสาหกรรมนี้เลยฟัง
- circle_of_competence ต้องมีตัวอย่างเหตุการณ์ผันผวน/ข่าวจริงในอดีตของอุตสาหกรรมนี้ประกอบ
  ไม่ใช่คำแนะนำลอย ๆ
- steelman_bear_case ต้องเขียนแรงและจริงจังเหมือนทนายฝ่ายค้าน ไม่ใช่ bear case แบบขอไปที
- เป็นกลาง ไม่เชียร์ซื้อขาย เพื่อการศึกษาเท่านั้น

{_LANG_RULE}"""

_OVERVIEW_CONTRACT = """รูปแบบ JSON ที่ต้องคืน (เท่านั้น):
{
  "definition": "<นิยามและขอบเขตของอุตสาหกรรม/สินค้านี้ อะไรนับอะไรไม่นับ 2-4 ประโยค>",
  "plain_narrative": "<เล่าแบบไม่มีศัพท์ ว่าระบบนี้ทำงานยังไงในชีวิตจริง เหมือนเล่าให้เพื่อนไม่รู้เรื่องฟัง 4-8 ประโยค>",
  "market_size_growth": "<ขนาดตลาดโดยประมาณ แนวโน้มการเติบโต ช่วงวงจรชีวิต (เติบโต/อิ่มตัว/ถดถอย) 2-4 ประโยค>",
  "life_cycle_stage": "<embryonic | growth | mature | declining>",
  "business_models": "<รูปแบบธุรกิจที่พบในอุตสาหกรรมนี้ (เช่น razor-blade, subscription, licensing) 2-4 ประโยค>",
  "macro_sensitivity": "<ผูกกับดอกเบี้ย/กำลังซื้อ/ราคาสินค้าโภคภัณฑ์ยังไง 2-3 ประโยค>",
  "historical_evolution": "<จุดเปลี่ยนสำคัญในอดีตของอุตสาหกรรมนี้ (ใครเคยครองตลาดแล้วเสียให้ใคร) 3-5 ประโยค>",
  "components": [
    {"name": "<ชื่อชั้น/component ของ ecosystem นี้>", "role_one_liner": "<ทำหน้าที่อะไร 1 ประโยค>"}
  ],
  "profit_pool": {
    "map": "<ใครในสายโซ่นี้กินกำไรส่วนใหญ่ไป และเพราะ moat แบบไหน 3-6 ประโยค อ้างชื่อบริษัทจริงประกอบ>",
    "evolution": "<profit pool นี้เปลี่ยนมือมายังไงในอดีต 2-4 ประโยค>"
  },
  "scenarios": [
    {"name": "ขาขึ้น (Bull)", "trigger": "<เงื่อนไข>", "impact": "<ผลต่อทั้ง ecosystem>"},
    {"name": "ฐาน (Base)", "trigger": "<เงื่อนไข>", "impact": "<ผลต่อทั้ง ecosystem>"},
    {"name": "ขาลง (Bear)", "trigger": "<เงื่อนไข>", "impact": "<ผลต่อทั้ง ecosystem>"}
  ],
  "circle_of_competence": {
    "normal_volatility": "<หุ้นในสายนี้เคยร่วงกี่ % กี่ครั้ง เพราะอะไร แล้วฟื้นยังไง ยกตัวอย่างจริงในอดีต 3-6 ประโยค>",
    "noise_vs_signal": [
      {"headline": "<ตัวอย่างข่าว/เหตุการณ์ที่เคยดูน่ากลัวในอดีต>", "verdict": "noise หรือ signal", "explain": "<ทำไม>"}
    ],
    "steelman_bear_case": "<ข้อโต้แย้งที่แข็งแรงที่สุดว่าทำไมไม่ควรลงทุนในอุตสาหกรรมนี้ เขียนจริงจัง 4-8 ประโยค>",
    "kill_criteria": ["<สัญญาณที่แปลว่า thesis พังจริง ต้องขาย — เขียนเป็นข้อ ๆ ให้เจาะจง 3-6 ข้อ>"],
    "mental_model_analogy": "<เทียบกลไกอุตสาหกรรมนี้กับสิ่งที่คนทั่วไปคุ้นเคยอยู่แล้ว 2-4 ประโยค>",
    "unpredictable_zones": "<ส่วนไหนของอุตสาหกรรมนี้ที่ไม่มีใครทำนายได้จริง ต้องกระจายความเสี่ยง 2-3 ประโยค>"
  }
}
components ให้มี 5-8 รายการ เรียงจาก upstream → downstream ของ value chain
ห้ามเดาตัวเลขมั่ว ถ้าไม่มั่นใจให้เขียนเป็นทิศทาง/ประมาณการเชิงคุณภาพแทน"""

_COMPONENT_SYSTEM = f"""คุณคือนักวิเคราะห์อุตสาหกรรมระดับ sell-side กำลังเขียน "1 บท" เจาะลึก
component เดียวใน ecosystem ของอุตสาหกรรมหนึ่ง ต้องอธิบายทุกแนวคิดผ่าน "บริษัทจริง" เป็นหลักฐาน
เสมอ ไม่ใช่ทฤษฎีลอย ๆ — ยกตัวอย่างบริษัทที่มีอยู่จริงในโลก เน้นบริษัทมหาชนสหรัฐที่มี ticker
ซื้อขายจริงเป็นหลัก (ระบุ ticker ให้ถูกต้องเท่าที่มั่นใจ ถ้าไม่มั่นใจ ticker ให้ใส่ null)

{_LANG_RULE}"""

_COMPONENT_CONTRACT_TMPL = """เขียนวิเคราะห์เจาะลึก component: "{comp_name}" ({comp_role})
ในอุตสาหกรรม/ธีม: "{theme_name}"

รูปแบบ JSON ที่ต้องคืน (เท่านั้น):
{{
  "role": "<บทบาทของ component นี้ในระบบนิเวศ อธิบายละเอียด 2-4 ประโยค>",
  "five_forces": {{
    "buyer_power": "<อำนาจต่อรองผู้ซื้อของชั้นนี้ สูง/กลาง/ต่ำ + เหตุผล 1-2 ประโยค>",
    "supplier_power": "<อำนาจต่อรอง supplier ของชั้นนี้เอง 1-2 ประโยค>",
    "new_entrants": "<ภัยจากผู้เล่นใหม่ 1-2 ประโยค>",
    "substitutes": "<ภัยจากสินค้า/เทคโนโลยีทดแทน 1-2 ประโยค>",
    "rivalry": "<การแข่งขันในกลุ่มผู้เล่นเดิม 1-2 ประโยค>"
  }},
  "market_structure": "<โครงสร้างตลาดชั้นนี้ (ผูกขาด/oligopoly/กระจัดกระจาย) และทำไม 2-3 ประโยค>",
  "companies": [
    {{"ticker": "<ticker จริงถ้ามั่นใจ เช่น TSM, AAPL หรือ null ถ้าไม่แน่ใจ/ไม่ใช่บริษัทมหาชน>",
     "name": "<ชื่อบริษัทจริง>",
     "role": "<leader | challenger | niche | failed_case>",
     "note": "<ทำไมบริษัทนี้ถึงอยู่ตำแหน่งนี้ moat/จุดอ่อนของเขา 2-3 ประโยค>"}}
  ],
  "financial_benchmark": "<ตัวเลขเชิงคุณภาพ/ทิศทาง เช่น ระดับ margin, capex intensity ทั่วไปของชั้นนี้ (บอกว่าเป็นค่าประมาณ ไม่ใช่ตัวเลขทางการ) 2-3 ประโยค>",
  "valuation_pattern": "<ตลาดมักให้ multiple ชั้นนี้สูง/ต่ำกว่าชั้นอื่นในสายโซ่ยังไงและทำไม 2-3 ประโยค>",
  "case_study": "<กรณีศึกษาจริงในอดีตของการเปลี่ยนขั้วอำนาจ/disruption ในชั้นนี้ อ้างชื่อบริษัทจริง 3-5 ประโยค>",
  "leading_indicators": "<ตัวชี้วัดล่วงหน้าที่ควรจับตาก่อนงบออก 1-3 ข้อ>",
  "second_order_beneficiaries": "<บริษัทที่ได้อานิสงส์ทางอ้อมจากชั้นนี้โดยไม่ได้อยู่ในชั้นนี้ตรง ๆ พร้อมชื่อจริง 2-3 ประโยค>",
  "pre_mortem": "<อะไรจะทำลาย economics ของชั้นนี้ได้ 2-3 ประโยค>",
  "glossary": [{{"term": "<ศัพท์เฉพาะที่ใช้ในบทนี้>", "def": "<นิยามสั้น ๆ>"}}]
}}
companies ให้มี 2-4 ราย มีทั้งผู้นำและผู้ท้าชิง (หรือเคสล้มเหลวถ้ามีในอดีต) ห้ามแต่งชื่อบริษัทที่ไม่มีอยู่จริง"""


def _cache_path(slug: str) -> Path:
    return _CACHE_DIR / f"ecosystem_{slug}.json"


def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t.lstrip("json").strip("` \n")
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        return json.loads(t[start:end + 1])
    raise ValueError("ไม่พบ JSON ในคำตอบของโมเดล")


def _s(v, n=2000):
    """แปลงเป็น string ตัดความยาว — LLM บางครั้งตอบ list แทน string ทั้งที่ contract สั่ง string
    (เช่น leading_indicators) เจอแล้วรวมเป็นข้อความอ่านได้แทนที่จะ stringify list ดิบ ๆ แบบ Python repr."""
    if v is None:
        return None
    if isinstance(v, list):
        v = " · ".join(str(x) for x in v if x)
    return str(v)[:n]


_NULL_TICKER_STRINGS = {"null", "none", "n/a", "-", "na", ""}


def _to_sym(s) -> str | None:
    """แปลง ticker เป็นสัญลักษณ์มาตรฐาน — LLM บางครั้งตอบ string 'NULL'/'N/A' แทน JSON null จริง
    ทั้งที่ contract สั่ง null ให้กรองทิ้งเป็น None แทนที่จะปล่อยให้โชว์เป็น ticker ปลอม."""
    if not isinstance(s, str):
        return None
    sym = s.strip().upper().replace(".", "-")
    if sym.lower() in _NULL_TICKER_STRINGS:
        return None
    return sym or None


def list_themes() -> dict:
    """รายชื่อ 85 theme จัดกลุ่มตาม sector พร้อมสถานะว่า generate แล้วหรือยัง (built)."""
    by_sector: dict[str, list[dict]] = {s: [] for s in SECTOR_ORDER}
    built_count = 0
    for t in THEMES:
        cache = _cache_path(t["slug"])
        built = cache.exists()
        as_of = None
        if built:
            try:
                as_of = json.loads(cache.read_text(encoding="utf-8")).get("generated_at")
            except (OSError, json.JSONDecodeError):
                built = False
        if built:
            built_count += 1
        by_sector.setdefault(t["sector"], []).append({
            "slug": t["slug"], "name_th": t["name_th"], "built": built, "generated_at": as_of,
        })
    return {
        "sectors": [{"sector": s, "themes": by_sector.get(s, [])} for s in SECTOR_ORDER if by_sector.get(s)],
        "total": len(THEMES),
        "built_count": built_count,
    }


def _ground_companies(companies: list) -> list[dict]:
    """แนบข้อมูลจริงจากฐานข้อมูลแอป (GICS + P/E กลุ่ม) ให้บริษัทที่ LLM ยกมา ถ้า ticker
    match กับหุ้นในฐานข้อมูล (S&P 500 constituents) — ไม่ match ก็ยังเก็บชื่อไว้อธิบายได้
    แต่ grounded=False (ไม่มีลิงก์วิเคราะห์ต่อในแอป)."""
    const = industry_peers.load_constituents()
    pe_map = industry_peers.industry_pe_map()
    out = []
    for c in companies if isinstance(companies, list) else []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        sym = _to_sym(c.get("ticker"))
        meta = const.get(sym) if sym else None
        role = str(c.get("role") or "").strip().lower()
        if role not in ("leader", "challenger", "niche", "failed_case"):
            role = "niche"
        entry = {
            "ticker": sym if meta else (sym or None),
            "name": _s(c.get("name"), 120),
            "role": role,
            "note": _s(c.get("note"), 400),
            "grounded": bool(meta),
        }
        if meta:
            entry["gics_sector"] = meta["sector"]
            entry["gics_sub_industry"] = meta["sub_industry"]
            pe_info = pe_map.get(sym)
            if pe_info:
                entry["industry_pe_median"] = pe_info.get("industry_pe")
        out.append(entry)
    return out[:6]


def _clean_overview(payload: dict) -> dict:
    components = []
    for c in (payload.get("components") or [])[:8]:
        if isinstance(c, dict) and c.get("name"):
            components.append({"name": _s(c.get("name"), 100), "role_one_liner": _s(c.get("role_one_liner"), 200)})
    scenarios = []
    for s in (payload.get("scenarios") or [])[:3]:
        if isinstance(s, dict) and s.get("name"):
            scenarios.append({"name": _s(s.get("name"), 40), "trigger": _s(s.get("trigger"), 300),
                              "impact": _s(s.get("impact"), 400)})
    coc = payload.get("circle_of_competence") or {}
    noise_vs_signal = []
    for n in (coc.get("noise_vs_signal") or [])[:6]:
        if isinstance(n, dict) and n.get("headline"):
            noise_vs_signal.append({"headline": _s(n.get("headline"), 200), "verdict": _s(n.get("verdict"), 20),
                                    "explain": _s(n.get("explain"), 300)})
    kill_criteria = [_s(k, 200) for k in (coc.get("kill_criteria") or [])[:6] if k]
    return {
        "definition": _s(payload.get("definition"), 800),
        "plain_narrative": _s(payload.get("plain_narrative"), 1500),
        "market_size_growth": _s(payload.get("market_size_growth"), 800),
        "life_cycle_stage": _s(payload.get("life_cycle_stage"), 30),
        "business_models": _s(payload.get("business_models"), 800),
        "macro_sensitivity": _s(payload.get("macro_sensitivity"), 600),
        "historical_evolution": _s(payload.get("historical_evolution"), 1000),
        "components": components,
        "profit_pool": {
            "map": _s((payload.get("profit_pool") or {}).get("map"), 1500),
            "evolution": _s((payload.get("profit_pool") or {}).get("evolution"), 800),
        },
        "scenarios": scenarios,
        "circle_of_competence": {
            "normal_volatility": _s(coc.get("normal_volatility"), 1200),
            "noise_vs_signal": noise_vs_signal,
            "steelman_bear_case": _s(coc.get("steelman_bear_case"), 1500),
            "kill_criteria": kill_criteria,
            "mental_model_analogy": _s(coc.get("mental_model_analogy"), 600),
            "unpredictable_zones": _s(coc.get("unpredictable_zones"), 600),
        },
    }


def _clean_component(payload: dict) -> dict:
    ff = payload.get("five_forces") or {}
    glossary = []
    for g in (payload.get("glossary") or [])[:8]:
        if isinstance(g, dict) and g.get("term"):
            glossary.append({"term": _s(g.get("term"), 60), "def": _s(g.get("def"), 200)})
    return {
        "role": _s(payload.get("role"), 600),
        "five_forces": {
            "buyer_power": _s(ff.get("buyer_power"), 300),
            "supplier_power": _s(ff.get("supplier_power"), 300),
            "new_entrants": _s(ff.get("new_entrants"), 300),
            "substitutes": _s(ff.get("substitutes"), 300),
            "rivalry": _s(ff.get("rivalry"), 300),
        },
        "market_structure": _s(payload.get("market_structure"), 500),
        "companies": _ground_companies(payload.get("companies")),
        "financial_benchmark": _s(payload.get("financial_benchmark"), 500),
        "valuation_pattern": _s(payload.get("valuation_pattern"), 500),
        "case_study": _s(payload.get("case_study"), 1000),
        "leading_indicators": _s(payload.get("leading_indicators"), 400),
        "second_order_beneficiaries": _s(payload.get("second_order_beneficiaries"), 500),
        "pre_mortem": _s(payload.get("pre_mortem"), 500),
        "glossary": glossary,
    }


async def _llm_json(system: str, user_msg: str, max_tokens: int) -> tuple[dict, set]:
    """เรียก LLM แล้ว parse JSON — ลอง provider สำรองถ้าตัวแรกโดน quota (เหมือน macro_business)."""
    exclude: set = set()
    last_err: Exception | None = None
    settings = get_settings()
    for attempt in range(2):
        try:
            text = await llm.complete(system, user_msg, exclude=exclude, max_tokens=max_tokens)
            return _extract_json(text), exclude
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            reason = str(exc).lower()
            is_quota = "429" in reason or "quota" in reason or "resource_exhausted" in reason
            cur = settings.resolve_llm(exclude=exclude)["provider"]
            if is_quota and attempt == 0 and cur not in ("none", ""):
                exclude.add(cur)
                continue
            raise
    raise last_err or ValueError("LLM ไม่ตอบ")


async def generate_theme(slug: str) -> dict:
    """generate ธีมเดียวสด ๆ: 1 call ภาพรวม + component list, แล้ว 1 call ต่อ component
    ลงลึกแยกกัน (เนื้อหาลึกระดับนี้เขียนทีเดียวจบไม่ได้ในคำตอบเดียว)."""
    theme = _THEME_BY_SLUG.get(slug)
    if not theme:
        raise ValueError(f"ไม่รู้จัก theme '{slug}'")
    settings = get_settings()
    if not settings.llm_enabled():
        raise ValueError("ต้องตั้งค่าคีย์ AI (เช่น Gemini ฟรี) เพื่อสร้าง Ecosystem Map")

    ov_system = _OVERVIEW_SYSTEM + "\n\n" + _OVERVIEW_CONTRACT
    ov_user = f'สร้าง industry primer สำหรับธีม: "{theme["name_th"]}" (sector: {theme["sector"]})'
    overview = None
    last_err: Exception | None = None
    for attempt in range(3):  # เจอทั้ง JSON เนื้อหาว่าง/ไม่ครบ และ connection error เป็นระยะ — ลองใหม่ก่อนยอมแพ้
        try:
            ov_raw, _ = await _llm_json(ov_system, ov_user, max_tokens=8000)
            candidate = _clean_overview(ov_raw)
            if candidate.get("definition") and candidate.get("components"):
                overview = candidate
                break
            last_err = ValueError("AI ตอบภาพรวมของธีมไม่ครบ (ไม่มี definition หรือ components)")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        if attempt < 2:
            await asyncio.sleep(2)
    if overview is None:
        raise ValueError(f"สร้างภาพรวมของธีม '{slug}' ไม่สำเร็จ: {last_err}")

    components = []
    for comp in overview["components"]:
        comp_user = _COMPONENT_CONTRACT_TMPL.format(
            comp_name=comp["name"], comp_role=comp["role_one_liner"] or "", theme_name=theme["name_th"])
        detail = None
        comp_err: Exception | None = None
        for attempt in range(3):  # เหมือน overview — เจอทั้งเนื้อหาว่าง/ไม่ครบ และ connection error เป็นระยะ
            try:
                comp_raw, _ = await _llm_json(_COMPONENT_SYSTEM, comp_user, max_tokens=4000)
                candidate = _clean_component(comp_raw)
                if candidate.get("role") and candidate.get("companies"):
                    detail = candidate
                    break
                comp_err = ValueError("AI ตอบเนื้อหาชั้นนี้ไม่ครบ (ไม่มี role หรือ companies)")
            except Exception as exc:  # noqa: BLE001 — component เดียวพังไม่ควรล้มทั้งธีม
                comp_err = exc
            if attempt < 2:
                await asyncio.sleep(2)
        if detail is None:
            detail = {"error": f"สร้างเนื้อหาชั้นนี้ไม่สำเร็จ: {comp_err}"}
        components.append({"name": comp["name"], "role_one_liner": comp["role_one_liner"], **detail})

    result = {
        "slug": slug,
        "name_th": theme["name_th"],
        "sector": theme["sector"],
        "generated_at": int(time.time()),
        **{k: v for k, v in overview.items() if k != "components"},
        "components": components,
        "disclaimer": (
            "เนื้อหานี้สร้างโดย AI เพื่อการศึกษาโครงสร้างอุตสาหกรรม ไม่ใช่คำแนะนำการลงทุน "
            "ตัวเลขเชิงปริมาณเป็นการประมาณการทิศทาง ไม่ใช่ตัวเลขทางการ — ควรตรวจสอบข้อมูลจริง "
            "ของบริษัทที่สนใจเพิ่มเติมก่อนตัดสินใจ"
        ),
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(slug).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


async def get_theme(slug: str, *, refresh: bool = False) -> dict:
    """อ่าน cache ถ้ามีและยังไม่หมดอายุ ไม่งั้น generate ใหม่ (on-demand)."""
    if slug not in _THEME_BY_SLUG:
        raise ValueError(f"ไม่รู้จัก theme '{slug}'")
    cache = _cache_path(slug)
    if not refresh and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - data.get("generated_at", 0) < _TTL:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return await generate_theme(slug)
