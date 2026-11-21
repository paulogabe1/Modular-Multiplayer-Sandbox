// Fill out your copyright notice in the Description page of Project Settings.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "Curves/CurveFloat.h"
#include "SharedCameraManager.generated.h"

UENUM(BlueprintType)
enum class ESharedCameraType : uint8
{
	ECT_Fixed UMETA(DisplayName = "Fixed"), //RootScene is fixed (EX : Street Fighter 2)
	ECT_2_5D UMETA(DisplayName = "2.5D"), //RootScene is not fixed; For 2D (EX : Tekken 7)
	ECT_3D UMETA(DisplayName = "3D") //RootScene is not fixed; For 3D (EX : Naruto Shippuden Ultimate Ninja Storm 4)
};

UCLASS()
class SHAREDCAMERASYSTEM_API ASharedCameraManager : public APawn
{
	GENERATED_BODY()

public:
	// Sets default values for this pawn's properties
	ASharedCameraManager();

	// Called every frame
	virtual void Tick(float DeltaTime) override;

	//SERVER
	void SetPlayer(AActor* Player);

	UFUNCTION(Server, Reliable, WithValidation)
	void ServerSetPlayer(AActor* Player);

protected:
	// Called when the game starts or when spawned
	virtual void BeginPlay() override;

	virtual void OnConstruction(const FTransform& Transform) override;

#if WITH_EDITOR
	virtual void PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent) override;
#endif

	// Always centered between players & rotates to keep players visible
	UPROPERTY(EditDefaultsOnly, Category = "Components")
	class USceneComponent* DefaultSceneRoot;

	UPROPERTY(EditDefaultsOnly, Category = "Components")
	class USpringArmComponent* SpringArmComp;

	UPROPERTY(EditDefaultsOnly, Category = "Components")
	class UCameraComponent* CameraComp;

	// SceneComponent for reference in camera rotation and movement, when SharedCameraType is 3D
	UPROPERTY(EditDefaultsOnly, Category = "Components")
	class USceneComponent* ReferenceScene;

	UPROPERTY(EditDefaultsOnly, BlueprintReadOnly, Category = "Components")
	class UStaticMeshComponent* Bounds;

	UPROPERTY(EditDefaultsOnly, BlueprintReadOnly, Category = "Components")
	class UNavigationInvokerComponent* NavigationInvoker;

	// Characters participating in the game
	UPROPERTY(Replicated, EditInstanceOnly, Category = "Settings", Transient)
	TArray<AActor*> Players;

	UPROPERTY(EditAnywhere, Category = "Settings")
	ESharedCameraType SharedCameraType = ESharedCameraType::ECT_3D;

	// Rotation Strength in 2.5D mode
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Settings|Core|2.5D")
	float RotateForce = 200.f;

	// Height of DefaultSceneRoot
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D", meta = (Units = "cm"))
	float Height = 25.f;

	// Minimum Length of SpringArm
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D", meta = (ClampMin = 0, Units = "cm"))
	float SpringArmBaseLength = 270.f;

	// Range of Length of SpringArm. i.e. Max Length - Min Length
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D", meta = (Units = "cm"))
	float SpringArmExtraLength = 130.f;

	// Minimum Angle of the pitch of SpringArm
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D")
	float MinSpringArmRotation = 3.f;

	// Maximum Angle of the pitch of SpringArm
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D")
	float MaxSpringArmRotation = 5.f;

	// Maximum Lateral distance allowed between P1 and P2 before edge rotation happens. Causes the camera to rotate to prevent moving out of frame
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D", meta = (ClampMin = 0, Units = "cm"))
	float EdgeRotationDistance = 145.f;

	// Edge Rotation Strength divider
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D", meta = (ClampMin = 0))
	float RotationDelay = 4.f;

	// Whether to scale Rotation Delay by Edge Rotation Offset
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D")
	bool ScaleRotationDelayByEdgeRotationOffset = true;

	// Multiplier for Edge Rotation Force
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D", meta = (ClampMin = 0))
	float EdgeRotationForceFactor = 1.f;

	// Whether to scale Edge Rotation Force by P1 to P2 distance
	UPROPERTY(EditAnywhere, Category = "Settings|Core|3D")
	bool ScaleEdgeRotationForceByDistance = false;

	// Min Lateral distance (when not in melee) allowed from P1 to P2 after overlap resolution
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap|General",
		meta = (/*ClampMin = 0, ClampMax = "MaxGeneralOverlapDistance", */Units = "cm"))
	float MinGeneralOverlapDistance = 100.f;

	// Max Lateral distance (when not in melee) allowed from P1 to P2 after overlap resolution
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap|General",
		meta = (/*ClampMin = "MinGeneralOverlapDistance", */Units = "cm"))
	float MaxGeneralOverlapDistance = 250.f;

	// Rotation Force Factor (when not in melee) to multiply to Overlap Rotation force
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap|General", meta = (ClampMin = 0))
	float GeneralOverlapRotationForceFactor = 18.f;

	// Min Lateral distance (when in melee) allowed from P1 to P2 after overlap resolution
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap|Melee",
		meta = (/*ClampMin = 0, ClampMax = "MaxMeleeOverlapDistance", */Units = "cm"))
	float MinMeleeOverlapDistance = 75.f;

	// Max Lateral distance (when in melee) allowed from P1 to P2 after overlap resolution
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap|Melee",
		meta = (/*ClampMin = "MinMeleeOverlapDistance", */Units = "cm"))
	float MaxMeleeOverlapDistance = 350.f;

	// Rotation Force Factor (when in melee) to multiply to Overlap Rotation force
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap|Melee", meta = (ClampMin = 0))
	float MeleeOverlapRotationForceFactor = 200.f;

	// How close players have to be for them to be considered in melee combat
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Settings|Overlap|Melee",
		meta = (ClampMin = 0, Units = "cm"))
	float MeleeCameraRotateDistance = 200.f;

	// Set when Melee Attacks happen via blueprints
	UPROPERTY(BlueprintReadWrite, Category = "Settings|Overlap|Melee")
	bool bIsMeleeAttacking = false;

	// Whether to use Camera Rotation Lag (in Spring Arm Component settings) when resolving overlap rotations
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap")
	bool bUseCameraRotationLagDuringOverlap = true;

	// Augment Camera Overlap rotation values with curve values
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap")
	bool bUseCurveSmoothing = true;

	// Curve to augment overlap rotation (when not in melee) distance based on distance between the two players
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap",
		meta = (EditCondition = "bUseCurveSmoothing == true", EditConditionHides))
	UCurveFloat* GeneralOverlapRotationCurve = nullptr;

	// Curve to augment overlap rotation (when in melee) distance based on distance between the two players
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap",
		meta = (EditCondition = "bUseCurveSmoothing == true", EditConditionHides))
	UCurveFloat* MeleeOverlapRotationCurve = nullptr;

	// Curve to augment rotation distance based on distance between the two players
	UPROPERTY(EditAnywhere, Category = "Settings|Overlap",
		meta = (EditCondition = "bUseCurveSmoothing == true", EditConditionHides))
	UCurveFloat* OverlapDistanceFactorCurve = nullptr;

	// Lag Speed (when not in melee) to smooth out Spring Arm Length changes
	UPROPERTY(EditAnywhere, Category = "Settings|Spring Arm Length|Lag", meta = (ClampMin = 0))
	float SpringArmLengthLagSpeed = 5.f;

	// Lag Speed (when in melee) to smooth out Spring Arm Length changes
	UPROPERTY(EditAnywhere, Category = "Settings|Spring Arm Length|Lag", meta = (ClampMin = 0))
	float MeleeSpringArmLengthLagSpeed = 0.5f;

	// Interp Speed from previous Lag Speed value. i.e. Interp Speed (when not in melee) from the melee value to the general value
	UPROPERTY(EditAnywhere, Category = "Settings|Spring Arm Length|Interp", meta = (ClampMin = 0))
	float SpringArmLengthInterpSpeed = 0.5f;

	// Interp Speed from previous Lag Speed value. i.e. Interp Speed (when in melee) from the melee value to the general value
	UPROPERTY(EditAnywhere, Category = "Settings|Spring Arm Length|Interp", meta = (ClampMin = 0))
	float MeleeSpringArmLengthInterpSpeed = 5.f;

	// Radius of Bounds collision
	UPROPERTY(EditAnywhere, Category = "Settings|Bounds", meta = (ClampMin = 0, Units = "cm"))
	float BoundsRadius = 800.f;

	// Half-height of Bounds collision
	UPROPERTY(EditAnywhere, Category = "Settings|Bounds", meta = (ClampMin = 0, Units = "cm"))
	float BoundsHalfHeight = 500.f;

	// Enable Debug
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Settings|Debug")
	bool Debug = false;

private:
	/** Common Variables */
	int8 IsForward; // Based on the CameraComp, where is the P1 front(1) and back(-1)
	int8 IsLeft; // Based on the ReferenceScene, where is the P1 left(1) and right(-1)
	float GlobalDistanceFactor; // Convert the distance between P1 and P2 to a ratio of 0 to 1
	float P1ToRoot_Dot; // How close the P1 to root direction is to the root's right vector

	/** Variables used in 3D */
	bool bIsEdgeRotating = false;
	float PreviousDesiredLength;
	float PreviousLagSpeed;
	uint16 NumOverlapTicks = 0; // Ticks since player overlap started
	bool bDefaultEnableCameraRotationLag;
	float OverlapDistance;
	float P1ToP2_Distance;
	bool bArePlayersOverlapping = false;


	void CalculateVal();
	void SetReferenceScene(); // Always set the ReferenceScene to be on the same x-axis as P1
	void RotateDefaultScene(); // Always set the ReferenceScene to always reside on the peripheral Y-axis of P1
	void SetCameraPosition(); // Adjust the vertical angle by adjusting the length of the spring arm and the pitch value
	void SetViewAllPlayers(); // Automatic rotation if P1 and P2 overlap each other based on the camera's perspective
	void SetNonOverlap(); // If bArePlayersOverlapping is True, rotate the camera to prevent players from overlapping.
	void SetP1RelativeVal(); // Determine where P1 is relative to P2 (front/back, left/right)
	void SetSpringArmLength(float DesiredLength); // Set the length of the spring arm component with a lag
	FVector GetRelativeCameraPosition() const;

	FORCEINLINE ESharedCameraType GetSharedCameraType() { return SharedCameraType; }
};
