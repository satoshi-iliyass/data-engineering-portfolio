select
    id                          as product_id,
    trim(name)                  as product_name,
    trim(category)              as category,
    cast(list_price as decimal(10, 2)) as list_price
from {{ ref('raw_products') }}
