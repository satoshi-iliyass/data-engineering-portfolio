select
    id                                     as order_item_id,
    order_id,
    product_id,
    quantity,
    cast(unit_price as decimal(10, 2))     as unit_price,
    cast(quantity * unit_price as decimal(12, 2)) as line_amount
from {{ ref('raw_order_items') }}
