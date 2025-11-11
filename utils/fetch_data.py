import psycopg2
import yaml
import os

# --- Database connection config ---
DB_CONFIG = {
    "host": "192.168.0.113",
    "port": 5432,
    "dbname": "object_detection",
    "user": "postgres",
    # "password": "admin",
    "password": os.getenv('POSTGRES_DB_PASS', "")
}

config_path="config_files/config.yaml"

with open(config_path, "r") as f:
    cfg = yaml.safe_load(f)

def get_db_connection():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn


# --- Fetch Technician Data ---
def fetch_technician_data(unit_name):
    """Fetch technician user_id and username for a given unit name."""
    conn = get_db_connection()
    cur = conn.cursor()

    query = """
        SELECT du.user_id, au.username
FROM detection_userprofile du
join auth_user au on au.id = du.user_id
where du.units_display like '%Cart-001%' and du.role != 'Admin' and du.role != 'Supervisor';
    """
    cur.execute(query)
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]

    data = [dict(zip(columns, row)) for row in rows]

    cur.close()
    conn.close()
    return data

def fetch_tray_data(unit_id):
    conn = get_db_connection()
    cur = conn.cursor()

    query = f"""
    select dt.tray_id
    from detection_unit du 
    join detection_tray dt 
    on du.id = dt.unit_id 
    where du.unit_id = '%s'
    order by dt.tray_id asc;
    """
    cur.execute(query, unit_id)
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]

    data = [dict(zip(columns, row)) for row in rows]

    cur.close()
    conn.close()
    return data

def fetch_tools(unit_id):
    conn = get_db_connection()
    cur = conn.cursor()

    query = """
    select dt3.tray_id, dt2.tool_id,  dt.tool_name 
    from detection_toolcreation dt 
join detection_traytool dt2 
on dt.tool_id = dt2.tool_id
join detection_tray dt3 
on dt3.id = dt2.tray_id
where dt3.unit_code = 'U001';
    """
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # --- Group by tray_id ---
    tools_details = {}
    for tray_id, tool_id, tool_name in rows:
        if tray_id not in tools_details:
            tools_details[tray_id] = []
        tools_details[tray_id].append({
            "tool_id": tool_id,
            "tool_name": tool_name
        })

    # --- Save to YAML ---
    yaml_data = {"tools_details": tools_details}

    with open("config_files/tools_config.yaml", "w") as f:
        yaml.dump(yaml_data, f, sort_keys=False, default_flow_style=False)

    print("✅ tools_config.yaml created successfully!")

# --- Save YAML ---
def save_yaml(data, filename):
    """Save technician data to a YAML file."""
    output_path = f"config_files/{filename}.yaml"
    config = {f"{filename}": data}
    with open(output_path, "w") as file:
        yaml.dump(config, file, default_flow_style=False, sort_keys=False)
    print(f"✅ {output_path} saved successfully.")

def save_marker_yaml(tray_data, markers_list):
    tray_mapped = {}
    for i, tray in enumerate(tray_data, start=1):
        marker_value = markers_list.get(i)
        if marker_value:
            tray_mapped[tray["tray_id"]] = marker_value

    # --- 4. Save to marker_mapped.yaml under key "TRAY_MAPPED" ---
    output_data = {"TRAY_MAPPED": tray_mapped}

    output_path = "config_files/marker_mapped.yaml"
    with open(output_path, "w") as f:
        yaml.dump(output_data, f, sort_keys=False)

    print("✅ marker_mapped.yaml saved successfully!")

# --- Main ---
def main():
    """Main execution function."""
    unit_name = str(cfg["Tool_Unit_name"]).strip()
    data = fetch_technician_data(unit_name)
    save_yaml(data, "technician_data")

    unit_id = cfg["Tool_Unit_id"]
    t_data = fetch_tray_data(unit_id)
    fetch_tools(unit_id)
    m_list = cfg.get("TRAY_MARKERS", {})
    save_marker_yaml(t_data, m_list)
    # tool_class_details = cfg.get("tool_class_details", {})


if __name__ == "__main__":
    main()
