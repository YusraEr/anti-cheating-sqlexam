-- ========================================================
-- ClassicModels Sample Database for PostgreSQL
-- Ready for Offline Database Practical Exams
-- ========================================================

DROP TABLE IF EXISTS orderdetails CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS productlines CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS employees CASCADE;
DROP TABLE IF EXISTS offices CASCADE;

-- 1. Offices
CREATE TABLE offices (
    office_code VARCHAR(10) PRIMARY KEY,
    city VARCHAR(50) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    address_line1 VARCHAR(50) NOT NULL,
    address_line2 VARCHAR(50),
    state VARCHAR(50),
    country VARCHAR(50) NOT NULL,
    postal_code VARCHAR(15) NOT NULL,
    territory VARCHAR(10) NOT NULL
);

-- 2. Employees
CREATE TABLE employees (
    employee_number INT PRIMARY KEY,
    last_name VARCHAR(50) NOT NULL,
    first_name VARCHAR(50) NOT NULL,
    extension VARCHAR(10) NOT NULL,
    email VARCHAR(100) NOT NULL,
    office_code VARCHAR(10) NOT NULL REFERENCES offices(office_code),
    reports_to INT REFERENCES employees(employee_number),
    job_title VARCHAR(50) NOT NULL
);

-- 3. Customers
CREATE TABLE customers (
    customer_number INT PRIMARY KEY,
    customer_name VARCHAR(50) NOT NULL,
    contact_last_name VARCHAR(50) NOT NULL,
    contact_first_name VARCHAR(50) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    address_line1 VARCHAR(50) NOT NULL,
    address_line2 VARCHAR(50),
    city VARCHAR(50) NOT NULL,
    state VARCHAR(50),
    postal_code VARCHAR(15),
    country VARCHAR(50) NOT NULL,
    sales_rep_employee_number INT REFERENCES employees(employee_number),
    credit_limit NUMERIC(10,2)
);

-- 4. Product Lines
CREATE TABLE productlines (
    product_line VARCHAR(50) PRIMARY KEY,
    text_description TEXT,
    html_description TEXT,
    image BYTEA
);

-- 5. Products
CREATE TABLE products (
    product_code VARCHAR(15) PRIMARY KEY,
    product_name VARCHAR(70) NOT NULL,
    product_line VARCHAR(50) NOT NULL REFERENCES productlines(product_line),
    product_scale VARCHAR(10) NOT NULL,
    product_vendor VARCHAR(50) NOT NULL,
    product_description TEXT NOT NULL,
    quantity_in_stock INT NOT NULL,
    buy_price NUMERIC(10,2) NOT NULL,
    msrp NUMERIC(10,2) NOT NULL
);

-- 6. Orders
CREATE TABLE orders (
    order_number INT PRIMARY KEY,
    order_date DATE NOT NULL,
    required_date DATE NOT NULL,
    shipped_date DATE,
    status VARCHAR(15) NOT NULL,
    comments TEXT,
    customer_number INT NOT NULL REFERENCES customers(customer_number)
);

-- 7. Order Details
CREATE TABLE orderdetails (
    order_number INT NOT NULL REFERENCES orders(order_number) ON DELETE CASCADE,
    product_code VARCHAR(15) NOT NULL REFERENCES products(product_code),
    quantity_ordered INT NOT NULL,
    price_each NUMERIC(10,2) NOT NULL,
    order_line_number SMALLINT NOT NULL,
    PRIMARY KEY (order_number, product_code)
);

-- 8. Payments
CREATE TABLE payments (
    customer_number INT NOT NULL REFERENCES customers(customer_number) ON DELETE CASCADE,
    check_number VARCHAR(50) NOT NULL,
    payment_date DATE NOT NULL,
    amount NUMERIC(10,2) NOT NULL,
    PRIMARY KEY (customer_number, check_number)
);

-- ========================================================
-- SEED DATA INSERTIONS
-- ========================================================

-- Offices
INSERT INTO offices (office_code, city, phone, address_line1, address_line2, state, country, postal_code, territory) VALUES
('1', 'San Francisco', '+1 650 219 4782', '100 Market Street', 'Suite 300', 'CA', 'USA', '94080', 'NA'),
('2', 'Boston', '+1 215 837 0825', '1550 Court Place', 'Suite 102', 'MA', 'USA', '02107', 'NA'),
('3', 'NYC', '+1 212 555 3000', '523 East 53rd Street', 'apt. 5A', 'NY', 'USA', '10022', 'NA'),
('4', 'Paris', '+33 14 723 4404', '43 Rue Jouffroy D abbans', NULL, NULL, 'France', '75017', 'EMEA'),
('5', 'Tokyo', '+81 33 224 5000', '4-1 Kioicho', NULL, 'Chiyoda-Ku', 'Japan', '102-8578', 'Japan'),
('6', 'Sydney', '+61 2 9264 2451', '5-11 Wentworth Avenue', 'Floor #2', NULL, 'Australia', 'NSW 2010', 'APAC'),
('7', 'London', '+44 20 7877 2041', '25 Old Broad Street', 'Level 7', NULL, 'UK', 'EC2N 1HN', 'EMEA');

-- Employees
INSERT INTO employees (employee_number, last_name, first_name, extension, email, office_code, reports_to, job_title) VALUES
(1002, 'Murphy', 'Diane', 'x5800', 'dmurphy@classicmodelcars.com', '1', NULL, 'President'),
(1056, 'Patterson', 'Mary', 'x4611', 'mpatterso@classicmodelcars.com', '1', 1002, 'VP Sales'),
(1076, 'Firrelli', 'Jeff', 'x9273', 'jfirrelli@classicmodelcars.com', '1', 1002, 'VP Marketing'),
(1088, 'Patterson', 'William', 'x4871', 'wpatterson@classicmodelcars.com', '6', 1056, 'Sales Manager (APAC)'),
(1102, 'Bondur', 'Gerard', 'x5408', 'gbondur@classicmodelcars.com', '4', 1056, 'Sale Manager (EMEA)'),
(1143, 'Bow', 'Anthony', 'x5428', 'abow@classicmodelcars.com', '1', 1056, 'Sales Manager (NA)'),
(1165, 'Jennings', 'Leslie', 'x3291', 'ljennings@classicmodelcars.com', '1', 1143, 'Sales Rep'),
(1166, 'Thompson', 'Leslie', 'x4065', 'lthompson@classicmodelcars.com', '1', 1143, 'Sales Rep'),
(1188, 'Firrelli', 'Julie', 'x2173', 'jfirrelli@classicmodelcars.com', '2', 1143, 'Sales Rep'),
(1216, 'Patterson', 'Steve', 'x4334', 'spatterson@classicmodelcars.com', '2', 1143, 'Sales Rep'),
(1286, 'Tseng', 'Foon Yue', 'x2248', 'ftseng@classicmodelcars.com', '3', 1143, 'Sales Rep'),
(1323, 'Vanauf', 'George', 'x4102', 'gvanauf@classicmodelcars.com', '3', 1143, 'Sales Rep'),
(1337, 'Bondur', 'Loui', 'x6493', 'lbondur@classicmodelcars.com', '4', 1102, 'Sales Rep'),
(1370, 'Hernandez', 'Gerard', 'x2028', 'ghernande@classicmodelcars.com', '4', 1102, 'Sales Rep'),
(1401, 'Castillo', 'Pamela', 'x2759', 'pcastillo@classicmodelcars.com', '4', 1102, 'Sales Rep'),
(1501, 'Bott', 'Larry', 'x2311', 'lbott@classicmodelcars.com', '7', 1102, 'Sales Rep'),
(1504, 'Jones', 'Barry', 'x102', 'bjones@classicmodelcars.com', '7', 1102, 'Sales Rep'),
(1611, 'Fixter', 'Andy', 'x101', 'afixter@classicmodelcars.com', '6', 1088, 'Sales Rep'),
(1612, 'Marsh', 'Peter', 'x102', 'pmarsh@classicmodelcars.com', '6', 1088, 'Sales Rep'),
(1619, 'King', 'Tom', 'x103', 'tking@classicmodelcars.com', '6', 1088, 'Sales Rep'),
(1621, 'Nishi', 'Mami', 'x101', 'mnishi@classicmodelcars.com', '5', 1056, 'Sales Rep'),
(1625, 'Kato', 'Yoshimi', 'x102', 'ykato@classicmodelcars.com', '5', 1621, 'Sales Rep'),
(1702, 'Gerard', 'Martin', 'x2312', 'mgerard@classicmodelcars.com', '4', 1102, 'Sales Rep');

-- Customers
INSERT INTO customers (customer_number, customer_name, contact_last_name, contact_first_name, phone, address_line1, address_line2, city, state, postal_code, country, sales_rep_employee_number, credit_limit) VALUES
(103, 'Atelier graphique', 'Schmitt', 'Carine', '40.32.2555', '54, rue Royale', NULL, 'Nantes', NULL, '44000', 'France', 1370, 21000.00),
(112, 'Signal Gift Stores', 'King', 'Jean', '7025551838', '8489 Strong St.', NULL, 'Las Vegas', 'NV', '83030', 'USA', 1166, 71800.00),
(114, 'Australian Collectors, Co.', 'Ferguson', 'Peter', '03 9520 4555', '636 St Kilda Road', 'Level 3', 'Melbourne', 'Victoria', '3004', 'Australia', 1611, 117300.00),
(119, 'La Rochelle Gifts', 'Labrune', 'Janine', '40.67.8555', '67, rue des Cinquante Otages', NULL, 'Nantes', NULL, '44000', 'France', 1370, 118200.00),
(121, 'Baane Mini Imports', 'Bergulfsen', 'Jonas', '07-98 9555', 'Erling Skakkes gate 78', NULL, 'Stavern', NULL, '4110', 'Norway', 1504, 81700.00),
(124, 'Mini Gifts Distributors Ltd.', 'Nelson', 'Susan', '4155551450', '5677 Strong St.', NULL, 'San Rafael', 'CA', '97562', 'USA', 1165, 210500.00),
(128, 'Blauer See Auto, Co.', 'Keitel', 'Roland', '+49 69 66 90 2555', 'Lyonerstr. 34', NULL, 'Frankfurt', NULL, '60528', 'Germany', 1504, 59700.00),
(129, 'Mini Caravy', 'Leong', 'Julien', '88.60.1555', '24, place Kléber', NULL, 'Strasbourg', NULL, '67000', 'France', 1370, 53800.00),
(131, 'Land of Toys Inc.', 'Lee', 'Kwai', '2125557818', '897 Long Airport Avenue', NULL, 'NYC', 'NY', '10022', 'USA', 1323, 114900.00),
(141, 'Euro+ Shopping Channel', 'Freyre', 'Diego', '(91) 555 94 44', 'C/ Moralzarzal, 86', NULL, 'Madrid', NULL, '28034', 'Spain', 1370, 227600.00),
(144, 'Volvo Model Replicas, Co', 'Berglund', 'Christina', '0921-12 3555', 'Berguvsvägen  8', NULL, 'Luleå', NULL, 'S-958 22', 'Sweden', 1504, 53100.00),
(145, 'Danish Wholesale Imports', 'Petersen', 'Jytte', '31 12 3555', 'Vinbæltet 34', NULL, 'København', NULL, '1734', 'Denmark', 1401, 83400.00),
(146, 'Saveley & Henriot, Co.', 'Saveley', 'Mary', '78.32.5555', '2, rue du Commerce', NULL, 'Lyon', NULL, '69004', 'France', 1337, 123900.00),
(148, 'Dragon Miniatures', 'Natividad', 'Eric', '+65 221 7555', '5900 SIT Building', '#06-01', 'Singapore', NULL, '079903', 'Singapore', 1621, 103800.00),
(151, 'Muscle Machine Inc', 'Simpson', 'Allen', '2125558888', '4092 Furth Circle', 'Suite 400', 'NYC', 'NY', '10022', 'USA', 1286, 138500.00),
(157, 'Diecast Classics Inc.', 'Graham', 'Mike', '2155553399', '7585 Pomona St.', NULL, 'Allentown', 'PA', '70267', 'USA', 1188, 100600.00),
(161, 'Technics Stores Inc.', 'Hirano', 'Juri', '6505556809', '9408 Furth Circle', NULL, 'Burlingame', 'CA', '94217', 'USA', 1165, 84600.00),
(166, 'Hand Auto', 'Franco', 'Keith', '4155554344', '80970 South Cherry', NULL, 'San Francisco', 'CA', '94107', 'USA', 1165, 95000.00),
(167, 'Herkku Gifts', 'Oeztan', 'Veysel', '+47 2267 3215', 'Breivika Industrivei 1', NULL, 'Bergen', NULL, 'N 5807', 'Norway', 1504, 96800.00),
(168, 'American Souvenirs Inc', 'Franco', 'Keith', '2035557845', '149 Spinnaker Dr.', 'Suite 101', 'New Haven', 'CT', '06513', 'USA', 1286, 0.00);

-- Product Lines
INSERT INTO productlines (product_line, text_description, html_description, image) VALUES
('Classic Cars', 'Attention to detail is visible in our high-end collector scale diecast cars.', NULL, NULL),
('Motorcycles', 'Spectacular high-precision diecast motorcycle models featuring movable parts.', NULL, NULL),
('Planes', 'Detailed vintage military and commercial aircraft models.', NULL, NULL),
('Ships', 'Historical and modern replicas of famous naval and merchant ships.', NULL, NULL),
('Trains', 'Precision steam, diesel and electric model trains for serious hobbyists.', NULL, NULL),
('Trucks and Buses', 'Heavy duty utility, commercial trucks, tractors and vintage city buses.', NULL, NULL),
('Vintage Cars', 'Replicas of iconic pre-WWII antique motorcars with opening doors.', NULL, NULL);

-- Products
INSERT INTO products (product_code, product_name, product_line, product_scale, product_vendor, product_description, quantity_in_stock, buy_price, msrp) VALUES
('S10_1678', '1969 Harley Davidson Ultimate Chopper', 'Motorcycles', '1:10', 'Min Lin Diecast', 'Features turnable front wheel and steering, detailed engine and exhaust.', 7933, 48.81, 95.70),
('S10_1949', '1952 Alpine Renault 1300', 'Classic Cars', '1:10', 'Classic Metal Creations', 'Hand-painted metal finish with accurate interior dials and seats.', 7305, 98.58, 214.30),
('S10_2016', '1996 Moto Guzzi 1100i', 'Motorcycles', '1:10', 'Highway 66 Mini Classics', 'Official licensed replica featuring rubber tires and working suspension.', 6625, 68.99, 118.94),
('S10_4698', '2003 Harley-Davidson Eagle Bike', 'Motorcycles', '1:10', 'Red Start Diecast', 'Diecast metal frame, movable front fork, functional kickstand.', 5582, 91.02, 168.64),
('S10_4757', '1972 Alfa Romeo GTA', 'Classic Cars', '1:10', 'Motor City Art Classics', 'Detailed four cylinder engine, opening hood, doors and trunk.', 3252, 85.68, 136.00),
('S10_4962', '1962 LanciaA Delta 16V', 'Classic Cars', '1:10', 'Second Gear Diecast', 'Features authentic rally livery and mudguards.', 6791, 103.42, 147.74),
('S12_1099', '1968 Ford Mustang', 'Classic Cars', '1:12', 'Autoart Studio Design', 'Authentic fastback profile, 390 V8 motor replica, chrome trims.', 68, 95.34, 194.57),
('S12_1108', '2001 Ferrari Enzo', 'Classic Cars', '1:12', 'Second Gear Diecast', 'Precision crafted carbon-fiber look styling and dihedral doors.', 3619, 95.59, 207.80),
('S12_1666', '1958 Setra Bus', 'Trucks and Buses', '1:12', 'Welly Diecast Productions', 'Realistic retro exterior with passenger seats and opening side doors.', 8798, 77.90, 136.67),
('S12_2823', '2002 Suzuki XREO', 'Motorcycles', '1:12', 'Unimax Art Galleries', 'Official factory blue color scheme with soft rubber racing tires.', 9997, 66.27, 150.62),
('S12_3148', '1969 Corvair Monza', 'Classic Cars', '1:18', 'Welly Diecast Productions', 'Air-cooled flat-six rear engine detail, steering front wheels.', 6906, 89.14, 151.08),
('S12_3359', '1934 Ford V8 Coupe', 'Vintage Cars', '1:12', 'Min Lin Diecast', 'Flared fenders, detailed flathead V8 engine, rumble seat.', 5649, 34.35, 62.46),
('S12_3847', '1940 Ford Pickup Truck', 'Trucks and Buses', '1:12', 'Studio M Art Models', 'Art Deco styling, split windshield, opening tailgate and hood.', 2613, 58.33, 116.67),
('S18_1097', '1940s Ford truck', 'Trucks and Buses', '1:18', 'Studio M Art Models', 'Sturdy diecast farm truck replica with wooden stake bed panels.', 3998, 84.76, 121.08),
('S18_1129', '1993 Mazda RX-7', 'Classic Cars', '1:18', 'Highway 66 Mini Classics', 'Pop-up headlights, twin rotary engine block, sport steering wheel.', 3975, 83.51, 141.54),
('S18_1342', '1937 Lincoln Berline', 'Vintage Cars', '1:18', 'Motor City Art Classics', 'Luxury pre-war sedan with rear suicide doors and whitewall tires.', 8693, 60.62, 102.74),
('S18_1367', '1936 Mercedes-Benz 500K Special Roadster', 'Vintage Cars', '1:18', 'Studio M Art Models', 'Iconic flowing wings, mother-of-pearl dash instrument decals.', 8635, 24.26, 53.91),
('S700_1691', 'American Airlines Boeing 707', 'Planes', '1:700', 'Min Lin Diecast', 'Polished aluminum fuselage finish with Pan-Am/American period livery.', 5841, 68.30, 91.07),
('S700_2047', 'HMS Bounty', 'Ships', '1:700', 'Unimax Art Galleries', 'Detailed multi-masted rigging, fabric sails, cannons on deck.', 3501, 39.83, 90.52),
('S700_2824', '1982 Camaro Z28', 'Classic Cars', '1:18', 'Carousel DieCast Legends', 'T-top roof panels, authentic sport hood louvers and rear spoiler.', 6934, 46.53, 101.15);

-- Orders
INSERT INTO orders (order_number, order_date, required_date, shipped_date, status, comments, customer_number) VALUES
(10100, '2023-01-06', '2023-01-13', '2023-01-10', 'Shipped', NULL, 114),
(10101, '2023-01-09', '2023-01-18', '2023-01-11', 'Shipped', 'Check address on invoice', 128),
(10102, '2023-01-10', '2023-01-18', '2023-01-14', 'Shipped', NULL, 121),
(10103, '2023-01-29', '2023-02-07', '2023-02-02', 'Shipped', NULL, 141),
(10104, '2023-01-31', '2023-02-09', '2023-02-01', 'Shipped', NULL, 145),
(10105, '2023-02-11', '2023-02-21', '2023-02-12', 'Shipped', NULL, 141),
(10106, '2023-02-17', '2023-02-24', '2023-02-21', 'Shipped', NULL, 145),
(10107, '2023-02-24', '2023-03-03', '2023-02-26', 'Shipped', 'Difficult delivery route', 119),
(10108, '2023-03-03', '2023-03-12', '2023-03-08', 'Shipped', NULL, 141),
(10109, '2023-03-10', '2023-03-19', '2023-03-14', 'Shipped', NULL, 148),
(10110, '2023-03-18', '2023-03-24', '2023-03-20', 'Shipped', NULL, 112),
(10111, '2023-03-25', '2023-03-31', '2023-03-30', 'Shipped', NULL, 144),
(10112, '2023-04-08', '2023-04-14', '2023-04-13', 'Shipped', 'Customer requested express freight', 141),
(10113, '2023-04-13', '2023-04-22', NULL, 'In Process', NULL, 124),
(10114, '2023-04-15', '2023-04-23', '2023-04-19', 'Shipped', NULL, 166),
(10115, '2023-04-16', '2023-04-24', '2023-04-19', 'Shipped', NULL, 161),
(10116, '2023-04-19', '2023-04-25', '2023-04-20', 'Shipped', NULL, 151),
(10117, '2023-04-20', '2023-04-27', '2023-04-24', 'Shipped', NULL, 148),
(10118, '2023-04-21', '2023-04-29', NULL, 'In Process', 'Waiting for stock arrival', 124),
(10119, '2023-04-28', '2023-05-05', '2023-05-02', 'Shipped', NULL, 141);

-- Order Details
INSERT INTO orderdetails (order_number, product_code, quantity_ordered, price_each, order_line_number) VALUES
(10100, 'S18_1129', 30, 136.00, 3),
(10100, 'S18_1342', 22, 98.50, 2),
(10100, 'S18_1367', 49, 52.00, 1),
(10101, 'S18_1129', 25, 140.00, 1),
(10101, 'S10_1678', 35, 90.00, 2),
(10102, 'S10_1949', 28, 205.00, 1),
(10102, 'S10_2016', 41, 115.00, 2),
(10103, 'S10_1949', 26, 214.30, 11),
(10103, 'S10_4962', 42, 119.67, 4),
(10103, 'S12_1099', 27, 180.00, 1),
(10103, 'S12_1108', 35, 195.00, 2),
(10104, 'S12_1666', 30, 130.00, 1),
(10104, 'S12_2823', 45, 142.00, 2),
(10104, 'S12_3148', 21, 145.00, 3),
(10105, 'S10_4698', 41, 160.00, 1),
(10105, 'S10_4757', 36, 130.00, 2),
(10105, 'S12_3847', 43, 110.00, 3),
(10106, 'S18_1097', 29, 115.00, 1),
(10106, 'S700_1691', 34, 88.00, 2),
(10107, 'S10_1678', 30, 92.00, 1),
(10107, 'S10_1949', 39, 210.00, 2),
(10107, 'S10_4698', 27, 165.00, 3),
(10108, 'S12_1099', 33, 185.00, 1),
(10108, 'S12_3359', 24, 60.00, 2),
(10109, 'S700_2047', 48, 85.00, 1),
(10109, 'S700_2824', 38, 98.00, 2),
(10110, 'S18_1129', 26, 138.00, 1),
(10110, 'S18_1367', 32, 51.00, 2),
(10111, 'S10_2016', 34, 112.00, 1),
(10112, 'S10_4962', 29, 120.00, 1),
(10112, 'S12_1108', 31, 200.00, 2),
(10113, 'S12_1666', 22, 132.00, 1),
(10114, 'S12_3847', 28, 112.00, 1),
(10115, 'S10_1678', 36, 91.00, 1),
(10116, 'S10_4757', 25, 132.00, 1),
(10117, 'S18_1097', 40, 118.00, 1),
(10118, 'S12_1099', 15, 190.00, 1),
(10119, 'S700_1691', 37, 89.00, 1);

-- Payments
INSERT INTO payments (customer_number, check_number, payment_date, amount) VALUES
(114, 'GG31455', '2023-01-16', 10223.83),
(128, 'MA76551', '2023-01-20', 10549.01),
(121, 'DB933704', '2023-01-25', 10549.00),
(141, 'HQ336336', '2023-02-05', 14571.44),
(145, 'JM555205', '2023-02-12', 7329.06),
(141, 'FO828514', '2023-02-20', 16761.03),
(145, 'ND741289', '2023-03-01', 5332.00),
(119, 'LN373447', '2023-03-05', 15540.78),
(141, 'IP383789', '2023-03-15', 7329.90),
(148, 'PH534347', '2023-03-22', 7718.00),
(112, 'HQ55022', '2023-03-29', 5220.00),
(144, 'ED204348', '2023-04-05', 3808.00),
(141, 'GC60337', '2023-04-18', 9689.00),
(166, 'BO864823', '2023-04-25', 3136.00),
(161, 'EU382103', '2023-04-26', 3276.00),
(151, 'AL493079', '2023-04-28', 3300.00),
(148, 'KM172837', '2023-05-02', 4720.00),
(141, 'OM314589', '2023-05-08', 3293.00);

-- Give access to student_role
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'student_role') THEN
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO student_role;
    END IF;
END
$$;
