-- Demo e-commerce schema. Written to be valid on both PostgreSQL and DuckDB.
-- Explicit integer PKs (no auto-increment) so the same seed file loads on both.

CREATE TABLE categories (
    category_id  INTEGER PRIMARY KEY,
    name         VARCHAR NOT NULL
);

CREATE TABLE products (
    product_id   INTEGER PRIMARY KEY,
    name         VARCHAR NOT NULL,
    category_id  INTEGER NOT NULL REFERENCES categories(category_id),
    price        DECIMAL(10,2) NOT NULL,   -- current list price
    cost         DECIMAL(10,2) NOT NULL,   -- unit cost (net revenue / margin)
    in_stock     BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE customers (
    customer_id  INTEGER PRIMARY KEY,
    name         VARCHAR NOT NULL,
    email        VARCHAR NOT NULL,
    country      VARCHAR NOT NULL,         -- USA, UK, Canada, Germany, India, Australia
    signup_date  DATE NOT NULL
);

CREATE TABLE orders (
    order_id     INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date   DATE NOT NULL,
    status       VARCHAR NOT NULL          -- completed, pending, cancelled, refunded
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    product_id    INTEGER NOT NULL REFERENCES products(product_id),
    quantity      INTEGER NOT NULL,
    unit_price    DECIMAL(10,2) NOT NULL    -- price charged at time of sale
);
