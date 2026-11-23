import logging
# 导入 1 号的采集模块
from src.ingestion.collector import collect_data
# 导入你自己的模块
from src.processing.cleaner import process_row
from src.processing.store import save_posts

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
LOG = logging.getLogger(__name__)


def run_once(limit=500):
    """跑一轮：采集 -> 清洗 -> 入库"""
    LOG.info("Step 1: 开始采集...")
    # 获取 1 号新抓到的数据 (返回的只是本轮新写入的)
    new_raw_rows = collect_data(limit=limit)

    if not new_raw_rows:
        LOG.info("没有新数据，本轮结束。")
        return 0

    LOG.info(f"采集到 {len(new_raw_rows)} 条新数据，开始清洗...")

    # 清洗数据
    clean_results = []
    for raw in new_raw_rows:
        try:
            clean_data = process_row(raw)
            clean_results.append(clean_data)
        except Exception as e:
            LOG.warning(f"清洗 post_id={raw['post_id']} 失败: {e}")
            # 即使清洗失败，也塞个空数据进去，保证和 raw_rows 长度一致
            clean_results.append({'title_clean': '', 'text_clean': '', 'tokens': []})

    LOG.info("Step 2: 保存入库...")
    save_posts(new_raw_rows, clean_results)
    LOG.info(f"成功入库 {len(new_raw_rows)} 条数据。")

    return len(new_raw_rows)


if __name__ == '__main__':
    # 直接运行时，跑一轮测试
    run_once()