-- The source system sometimes exports the same order twice: keep one row per order_id
with ranked as (
    select *,
           row_number() over (partition by id order by order_date) as rn
    from {{ ref('raw_orders') }}
)
select
    id                         as order_id,
    customer_id,
    cast(order_date as date)   as order_date,
    lower(trim(status))        as status
from ranked
where rn = 1
