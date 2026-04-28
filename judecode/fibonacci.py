def fibonacci(n, memo={}):
    """
    คำนวณค่า Fibonacci ลำดับที่ n แบบมี memoization
    
    Args:
        n: ตำแหน่งที่ต้องการ (ตั้งแต่ 0)
        memo: dictionary เก็บค่าที่คำนวณแล้ว
    
    Returns:
        ค่า Fibonacci ลำดับที่ n
    """
    # กรณีฐาน
    if n == 0:
        return 0
    elif n == 1:
        return 1
    
    # ถ้าคำนวณแล้ว ให้คืนค่าเดิม
    if n in memo:
        return memo[n]
    
    # คำนวณและเก็บไว้ใน memo
    memo[n] = fibonacci(n - 1, memo) + fibonacci(n - 2, memo)
    return memo[n]


# ทดลอง
if __name__ == "__main__":
    print("=== Fibonacci Sequence ===")
    for i in range(11):
        print(f"F({i}) = {fibonacci(i)}")
    
    print(f"\nF(10) = {fibonacci(10)}")
    print(f"F(20) = {fibonacci(20)}")
    print(f"F(30) = {fibonacci(30)}")
    print(f"F(40) = {fibonacci(40)}")
