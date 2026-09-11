from backend.services.h3_utils import assign_tasks_to_fieldmen_h3

# Two fieldmen: A (closest), B (further)
fieldmen = [
    {"user_id":"5133fe5a-dd67-43ab-acbf-6348d71f6784","current_lat":14.7005,"current_long":121.0105},
    {"user_id":"6f6cd9ff-7c63-418b-9be6-59ac3bf72fd7","current_lat":14.71,"current_long":121.02},
]

tasks = [
    {"task_id":"t1","latitude":14.7006,"longitude":121.0104,"priority":1},
]

res = assign_tasks_to_fieldmen_h3(tasks, fieldmen, resolution=9)
print(res)
