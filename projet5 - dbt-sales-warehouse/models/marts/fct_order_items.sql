-- Fact table: one row per order line (the grain), with keys to the dimensions
select
    i.order_item_id,
    i.order_id,
    o.customer_id,
    i.product_id,
    o.order_date,
    o.status,
    i.quantity,
    i.unit_price,
    p.list_price,
    i.line_amount,
    cast(i.quantity * (p.list_price - i.unit_price) as decimal(12, 2)) as discount_amount,
    o.status in ('completed', 'shipped')                              as is_revenue
from {{ ref('stg_order_items') }} i
join {{ ref('stg_orders') }}   o using (order_id)
join {{ ref('stg_products') }} p using (product_id)
