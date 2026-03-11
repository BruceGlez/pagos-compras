SAT_REGIMENES_FISCALES = [
    ("601", "601 General de Ley Personas Morales"),
    ("603", "603 Personas Morales con Fines no Lucrativos"),
    ("605", "605 Sueldos y Salarios e Ingresos Asimilados a Salarios"),
    ("606", "606 Arrendamiento"),
    ("607", "607 Régimen de Enajenación o Adquisición de Bienes"),
    ("608", "608 Demás ingresos"),
    ("610", "610 Residentes en el Extranjero sin Establecimiento Permanente en México"),
    ("611", "611 Ingresos por Dividendos (socios y accionistas)"),
    ("612", "612 Personas Físicas con Actividades Empresariales y Profesionales"),
    ("614", "614 Ingresos por intereses"),
    ("615", "615 Régimen de los ingresos por obtención de premios"),
    ("616", "616 Sin obligaciones fiscales"),
    ("620", "620 Sociedades Cooperativas de Producción que optan por diferir sus ingresos"),
    ("621", "621 Incorporación Fiscal"),
    ("622", "622 Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras"),
    ("623", "623 Opcional para Grupos de Sociedades"),
    ("624", "624 Coordinados"),
    ("625", "625 Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas"),
    ("626", "626 Régimen Simplificado de Confianza"),
]

SAT_REGIMENES_MAP = {k: v for k, v in SAT_REGIMENES_FISCALES}

SAT_FORMAS_PAGO = [
    ("01", "01 Efectivo"),
    ("02", "02 Cheque nominativo"),
    ("03", "03 Transferencia electrónica de fondos"),
    ("04", "04 Tarjeta de crédito"),
    ("28", "28 Tarjeta de débito"),
    ("99", "99 Por definir"),
]

SAT_METODOS_PAGO = [
    ("PUE", "PUE Pago en una sola exhibición"),
    ("PPD", "PPD Pago en parcialidades o diferido"),
]

SAT_USOS_CFDI = [
    ("G01", "G01 Adquisición de mercancías"),
    ("S01", "S01 Sin efectos fiscales"),
]
